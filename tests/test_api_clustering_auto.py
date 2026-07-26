"""分類「自動」端點契約（#B 2026-07-26）：一個入口，後端自動判斷首次 vs 增量。

設計：POST /workspaces/{id}/clustering/auto
- 該 workspace 尚無既有分群（get_latest_topic_state 抛 TopicStateNotFoundError）→ 走 calibrate（首次）。
- 已有既有分群 → 走 incremental（增量，處理新專利，不重跑）。

用 mock 驗「分流邏輯」本身，不依賴真 DB 種子狀態（分流是純判斷，DB 整合另由 test_api_clustering 覆蓋）。
"""
from __future__ import annotations

import unittest
import warnings
from unittest import mock

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
    module=r"fastapi\.testclient",
)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api import clustering as clustering_api  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.repositories.topic_state_repository import (  # noqa: E402
    TopicStateNotFoundError,
)


client = TestClient(app)

WS = 940101
WIPS = "wips_independent_claims"


class ClusteringAutoRouteTests(unittest.TestCase):
    """auto 端點依「有無既有分群」自動分流 calibrate / incremental。"""

    def _patch_job(self):
        """替身 _create_clustering_job 與 embeddings 的 create_job：不碰 DB，記錄 job_type。"""
        captured = {}

        def fake(job_type, payload, *, workspace_id, idempotency_key=None):
            captured["job_type"] = job_type
            captured["payload"] = payload
            return {"job_id": 1, "job_type": job_type, "status": "queued"}

        # embeddings 先入列會呼叫 job_repository.create_job；替身回一個帶 job_id 的物件。
        fake_embed = mock.MagicMock(return_value=mock.MagicMock(job_id=99))
        return captured, [
            mock.patch.object(clustering_api, "_create_clustering_job", fake),
            mock.patch.object(clustering_api.job_repository, "create_job", fake_embed),
        ]

    def test_auto_first_time_runs_calibrate(self):
        """無既有分群（TopicStateNotFoundError）→ 建 clustering_calibrate（首次）。"""
        captured, patches = self._patch_job()

        def raise_not_found(workspace_id, source_field):
            raise TopicStateNotFoundError("no state")

        with patches[0], patches[1], mock.patch.object(
            clustering_api, "get_latest_topic_state", raise_not_found
        ):
            resp = client.post(f"/api/v1/workspaces/{WS}/clustering/auto",
                               json={"source_field": WIPS})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["job_type"], "clustering_calibrate")

    def test_auto_existing_runs_incremental(self):
        """已有既有分群 → 建 clustering_incremental（增量，處理新專利）。"""
        captured, patches = self._patch_job()

        def has_state(workspace_id, source_field):
            return {"workspace_id": workspace_id, "run_id": 1, "topics": []}

        with patches[0], patches[1], mock.patch.object(
            clustering_api, "get_latest_topic_state", has_state
        ):
            resp = client.post(f"/api/v1/workspaces/{WS}/clustering/auto",
                               json={"source_field": WIPS})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["job_type"], "clustering_incremental")
        # embeddings 先入列（分群前置），回應帶其 job_id
        self.assertEqual(resp.json().get("embeddings_job_id"), 99)

    def test_auto_unknown_source_field_422(self):
        """非法 source_field → 422（沿既有 _validate_source_field）。"""
        resp = client.post(f"/api/v1/workspaces/{WS}/clustering/auto",
                           json={"source_field": "not_a_channel"})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
