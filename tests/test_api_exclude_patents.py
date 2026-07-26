"""剔除（標不相干）API 端點契約（#E 2026-07-26）：使用者從前端剔除單/多筆專利。

補的斷鏈：exclusions.exclude_patents() 引擎已存在（寫排除表＋刪 assignment、不重跑分群），
但無 API 呼叫端、前端無入口。本端點是使用者剔除的入口。

用 mock 驗端點契約（呼叫 exclude_patents 帶對的參數、回傳筆數），不碰 DB。
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

from backend.app.api import workspaces as workspaces_api  # noqa: E402
from backend.app.main import app  # noqa: E402


client = TestClient(app)
WS = 940301


class ExcludePatentsRouteTests(unittest.TestCase):
    """POST /workspaces/{id}/exclude-patents：標不相干（剔除），不重跑分群。"""

    def test_exclude_single_patent_calls_engine(self):
        """帶 patent_ids＋reason → 呼叫 exclude_patents，回實際剔除筆數。"""
        fake = mock.MagicMock(return_value=1)
        with mock.patch.object(workspaces_api, "exclude_patents", fake):
            resp = client.post(
                f"/api/v1/workspaces/{WS}/exclude-patents",
                json={"patent_ids": [55], "reason": "與本分析不相干"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["excluded_count"], 1)
        # 端點須把 (patent_id, reason) entries 傳給引擎，且 workspace_id 正確
        args, kwargs = fake.call_args
        self.assertEqual(args[0], WS)
        entries = list(args[1])
        self.assertEqual(entries, [(55, "與本分析不相干")])

    def test_exclude_multiple_patents(self):
        """支援多筆一次剔除；reason 可省略（None）。"""
        fake = mock.MagicMock(return_value=2)
        with mock.patch.object(workspaces_api, "exclude_patents", fake):
            resp = client.post(
                f"/api/v1/workspaces/{WS}/exclude-patents",
                json={"patent_ids": [55, 66]},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["excluded_count"], 2)
        entries = list(fake.call_args.args[1])
        self.assertEqual(entries, [(55, None), (66, None)])

    def test_empty_patent_ids_422(self):
        """patent_ids 空 → 422（沒有可剔除的對象）。"""
        resp = client.post(
            f"/api/v1/workspaces/{WS}/exclude-patents", json={"patent_ids": []}
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
