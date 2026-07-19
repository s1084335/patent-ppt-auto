"""段 2 驗收：POST /api/v1/workspaces/compose 與 compose 服務。

自建有控制專利集的拋棄式來源 workspace（現有 workspaces 都是同 200 件、無法驗
聯集）。覆蓋：兩來源、三來源、重複來源 ID、重複專利去重、不存在來源、非 active
來源、rollback、來源不變、新 ws 可接續建 calibrate job。結尾清乾淨。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.app_layer import workspace_compose
from backend.app.clustering.workspace_service import create_workspace


PREFIX = "/api/v1"
client = TestClient(app)
CREATED_BY = "_v_compose"


def _connect():
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs())


class WorkspaceComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3) as conn:
                cls.pids = [
                    int(r[0])
                    for r in conn.execute(
                        "SELECT id FROM core_layer.patents ORDER BY id LIMIT 6"
                    ).fetchall()
                ]
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"DB unreachable: {exc}")
        if len(cls.pids) < 6:
            raise unittest.SkipTest("need at least 6 patents")

    _seq = 0

    def setUp(self):
        self._ws: list[int] = []

    def _uniq(self, base: str) -> str:
        type(self)._seq += 1
        return f"{base}_{type(self)._seq}"

    def tearDown(self):
        if not self._ws:
            return
        with _connect() as conn:
            conn.execute(
                "DELETE FROM app_layer.workspace_compose_sources "
                "WHERE workspace_id = ANY(%s) OR source_workspace_id = ANY(%s)",
                (self._ws, self._ws),
            )
            conn.execute(
                "DELETE FROM app_layer.processing_jobs WHERE workspace_id = ANY(%s)",
                (self._ws,),
            )
            conn.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = ANY(%s)", (self._ws,))
            conn.commit()

    def _make_source(self, patent_ids: list[int]) -> int:
        ws = create_workspace(
            workspace_name=self._uniq("_v_src"), patent_ids=patent_ids, created_by=CREATED_BY
        )
        self._ws.append(ws)
        return ws

    def _patent_count(self, workspace_id: int) -> int:
        with _connect() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM app_layer.workspace_patents WHERE workspace_id = %s",
                    (workspace_id,),
                ).fetchone()[0]
            )

    def _compose(self, source_ids: list[int], name: str | None = None):
        resp = client.post(
            f"{PREFIX}/workspaces/compose",
            json={
                "workspace_name": name or self._uniq("_v_composed"),
                "source_workspace_ids": source_ids,
                "created_by": CREATED_BY,
            },
        )
        if resp.status_code == 200:
            self._ws.append(resp.json()["workspace_id"])
        return resp

    # ── 聯集/去重 ─────────────────────────────────────────
    def test_two_sources_union_and_dedup(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])  # 1,2,3
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])  # 3,4,5（3 重疊）
        resp = self._compose([a, b])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["union_count"], 5)       # {1,2,3,4,5}
        self.assertEqual(body["duplicate_count"], 1)   # (3+3)-5
        self.assertEqual(
            {s["source_workspace_id"]: s["patent_count"] for s in body["source_counts"]},
            {a: 3, b: 3},
        )
        # 新 ws 實際成員數 = 聯集數
        self.assertEqual(self._patent_count(body["workspace_id"]), 5)
        # 來源完全不動
        self.assertEqual(self._patent_count(a), 3)
        self.assertEqual(self._patent_count(b), 3)
        # lineage 兩列
        with _connect() as conn:
            n = conn.execute(
                "SELECT count(*) FROM app_layer.workspace_compose_sources WHERE workspace_id=%s",
                (body["workspace_id"],),
            ).fetchone()[0]
        self.assertEqual(n, 2)

    def test_three_sources(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])  # 1,2,3
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])  # 3,4,5
        c = self._make_source([self.pids[4], self.pids[5]])                # 5,6
        body = self._compose([a, b, c]).json()
        self.assertEqual(body["union_count"], 6)       # {1..6}
        self.assertEqual(body["duplicate_count"], 2)   # (3+3+2)-6

    def test_duplicate_source_ids_deduped(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])
        body = self._compose([a, a, b]).json()  # 重複來源 ID
        self.assertEqual(body["union_count"], 5)
        self.assertEqual(len(body["source_counts"]), 2)  # 去重成 2 個來源
        with _connect() as conn:
            n = conn.execute(
                "SELECT count(*) FROM app_layer.workspace_compose_sources WHERE workspace_id=%s",
                (body["workspace_id"],),
            ).fetchone()[0]
        self.assertEqual(n, 2)

    # ── 驗證錯誤 ─────────────────────────────────────────
    def test_missing_source_404(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        resp = self._compose([a, 9_999_999])
        self.assertEqual(resp.status_code, 404)

    def test_inactive_source_409(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        b = self._make_source([self.pids[2], self.pids[3]])
        with _connect() as conn:
            conn.execute(
                "UPDATE app_layer.workspaces SET status='archived' WHERE workspace_id=%s", (b,)
            )
            conn.commit()
        resp = self._compose([a, b])
        self.assertEqual(resp.status_code, 409)

    def test_less_than_two_distinct_422(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        # 兩個相同來源 → 去重後 1 個 → 422（pydantic 過關但服務層擋）
        resp = self._compose([a, a])
        self.assertEqual(resp.status_code, 422)

    def test_single_source_422_by_pydantic(self):
        a = self._make_source([self.pids[0]])
        resp = client.post(
            f"{PREFIX}/workspaces/compose",
            json={"workspace_name": "_v_one", "source_workspace_ids": [a]},
        )
        self.assertEqual(resp.status_code, 422)  # min_length=2

    def test_duplicate_workspace_name_409(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        b = self._make_source([self.pids[2], self.pids[3]])
        # 用一個已存在 workspace 的名稱去 compose → workspace_name 唯一衝突 → 409
        with _connect() as conn:
            existing_name = conn.execute(
                "SELECT workspace_name FROM app_layer.workspaces WHERE workspace_id=%s", (a,)
            ).fetchone()[0]
        resp = self._compose([a, b], name=existing_name)
        self.assertEqual(resp.status_code, 409)

    # ── 單一 transaction rollback ─────────────────────────
    def test_rollback_leaves_no_partial_workspace(self):
        a = self._make_source([self.pids[0], self.pids[1]])
        b = self._make_source([self.pids[2], self.pids[3]])
        with mock.patch.object(workspace_compose, "_insert_lineage", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                workspace_compose.compose_workspaces(
                    workspace_name="_v_rollback", source_workspace_ids=[a, b], created_by=CREATED_BY
                )
        # 名稱為 _v_rollback 的 workspace 不應存在（workspace insert 已 rollback）
        with _connect() as conn:
            n = conn.execute(
                "SELECT count(*) FROM app_layer.workspaces WHERE workspace_name='_v_rollback'"
            ).fetchone()[0]
        self.assertEqual(n, 0)

    # ── 新 ws 可接續建分群 job ────────────────────────────
    def test_composed_ws_can_create_calibrate_job(self):
        a = self._make_source([self.pids[0], self.pids[1], self.pids[2]])
        b = self._make_source([self.pids[2], self.pids[3], self.pids[4]])
        new_ws = self._compose([a, b]).json()["workspace_id"]
        resp = client.post(
            f"{PREFIX}/workspaces/{new_ws}/clustering/calibrate",
            json={"source_field": "wips_independent_claims"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "clustering_calibrate")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["workspace_id"], new_ws)


if __name__ == "__main__":
    unittest.main()
