"""E3 API 測試：clustering／reports 端點只驗「建對 job」與輸入驗證、讀候選。

實際執行歸 worker，這裡不跑分群/報表。建立的 job 以回傳 job_id 追蹤，
結尾逐一刪除。需要 DB 的測試鎖在 RUN_DB_TESTS=1。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from dotenv import load_dotenv

from backend.app.reports.report_definitions import DEFAULT_REPORT_NAMES, REPORT_DEFINITIONS


PREFIX = "/api/v1"


class ClusteringReportsConstantTests(unittest.TestCase):
    """不須 DB 的純常數驗證。"""

    def test_default_report_names_match_definitions(self):
        """確認固定預設報表名單與定義順序完全一致。"""
        self.assertIsInstance(DEFAULT_REPORT_NAMES, tuple)
        # 14 現有屬性統計 ＋ 3 分群報表＝17 種全列（2026-07-24 報表種類定案）。
        self.assertEqual(len(DEFAULT_REPORT_NAMES), 17)
        self.assertEqual(DEFAULT_REPORT_NAMES, tuple(REPORT_DEFINITIONS))

    def test_get_report_definitions_returns_catalog(self):
        """GET /report-definitions 必須回傳完整報表目錄（前端探索入口）。"""
        from fastapi.testclient import TestClient
        from backend.app.main import app

        client = TestClient(app)
        resp = client.get(f"{PREFIX}/report-definitions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("reports", data)
        self.assertIn("default_report_names", data)
        self.assertIn("allowed_filter_columns", data)
        names = [item["name"] for item in data["reports"]]
        self.assertEqual(sorted(names), sorted(REPORT_DEFINITIONS))
        self.assertEqual(data["default_report_names"], list(DEFAULT_REPORT_NAMES))
        self.assertIn("country_code", data["allowed_filter_columns"])
        for item in data["reports"]:
            self.assertIn("label_zh", item)
            self.assertIn("report_type", item)
            self.assertIn(item["filter_mode"], ("patent_level", "family_translated"))


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS") == "1",
                     "set RUN_DB_TESTS=1 to run clustering/reports API tests")
class ClusteringReportsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        from fastapi.testclient import TestClient
        from backend.app.main import app

        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3) as conn:
                ws = conn.execute(
                    "SELECT workspace_id FROM app_layer.workspaces ORDER BY workspace_id LIMIT 1"
                ).fetchone()
                # 候選自 0021 併表起存在 topic_runs.topic_state_json->'candidates'，
                # 舊表 derived_layer.topic_candidates 已移除；沿用舊表會讓整個 class
                # 因 relation does not exist 被靜默 skip（等於沒驗）。
                run = conn.execute(
                    "SELECT tr.run_id, (c.value ->> 'candidate_id')::int "
                    "FROM derived_layer.topic_runs tr "
                    "CROSS JOIN LATERAL jsonb_array_elements("
                    "    COALESCE(tr.topic_state_json -> 'candidates', '[]'::jsonb)) AS c(value) "
                    "ORDER BY tr.run_id, 2 LIMIT 1"
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"DB unreachable: {exc}")
        if ws is None:
            raise unittest.SkipTest("no workspace to test against")
        cls.ws_id = int(ws[0])
        cls.run_id = int(run[0]) if run else None
        cls.candidate_id = int(run[1]) if run else None
        cls.client = TestClient(app)

    def setUp(self):
        self._created: list[int] = []

    def _connect(self):
        import psycopg
        from backend.app.db.connection import get_connection_kwargs
        return psycopg.connect(**get_connection_kwargs())

    def tearDown(self):
        # 佇列表自 0021 起為 app_layer.workflow_runs（job_id 即 run_id）；
        # 舊表 app_layer.processing_jobs 已移除，沿用舊名會讓每個建 job 的測試在 tearDown 炸掉。
        if self._created:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM app_layer.workflow_runs WHERE run_id = ANY(%s)",
                    (self._created,),
                )
                conn.commit()

    def _track(self, resp):
        if resp.status_code == 200:
            self._created.append(resp.json()["job_id"])
        return resp

    # ── calibrate ─────────────────────────────────────────
    def test_calibrate_creates_correct_job(self):
        resp = self._track(
            self.client.post(
                f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate",
                json={"source_field": "wips_independent_claims"},
            )
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "clustering_calibrate")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["payload"]["workspace_id"], self.ws_id)
        self.assertEqual(body["payload"]["source_field"], "wips_independent_claims")

    def test_calibrate_invalid_source_field_422(self):
        resp = self.client.post(
            f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate",
            json={"source_field": "technical"},  # 非白名單
        )
        self.assertEqual(resp.status_code, 422)

    def test_calibrate_unknown_workspace_404(self):
        resp = self.client.post(
            f"{PREFIX}/workspaces/999999/clustering/calibrate",
            json={"source_field": "effect_summary"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_calibrate_idempotency(self):
        body = {"source_field": "effect_summary", "idempotency_key": "_verify_e3_calib"}
        a = self._track(self.client.post(f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate", json=body))
        b = self._track(self.client.post(f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate", json=body))
        self.assertEqual(a.json()["job_id"], b.json()["job_id"])

    # ── incremental ───────────────────────────────────────
    def test_incremental_creates_correct_job(self):
        resp = self._track(
            self.client.post(
                f"{PREFIX}/workspaces/{self.ws_id}/clustering/incremental",
                json={"source_field": "effect_summary"},
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_type"], "clustering_incremental")

    # ── finalize ──────────────────────────────────────────
    def test_finalize_creates_correct_job(self):
        if self.run_id is None:
            self.skipTest("no run with candidates")
        resp = self._track(
            self.client.post(
                f"{PREFIX}/clustering/runs/{self.run_id}/finalize",
                json={"candidate_id": self.candidate_id, "selected_by": "tester"},
            )
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "clustering_finalize")
        self.assertEqual(body["payload"]["run_id"], self.run_id)
        self.assertEqual(body["payload"]["candidate_id"], self.candidate_id)

    def test_finalize_candidate_not_in_run_422(self):
        if self.run_id is None:
            self.skipTest("no run with candidates")
        resp = self.client.post(
            f"{PREFIX}/clustering/runs/{self.run_id}/finalize",
            json={"candidate_id": -1, "selected_by": "tester"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_finalize_unknown_run_404(self):
        resp = self.client.post(
            f"{PREFIX}/clustering/runs/999999/finalize",
            json={"candidate_id": 1},
        )
        self.assertEqual(resp.status_code, 404)

    # ── candidates 讀取 ───────────────────────────────────
    def test_get_candidates(self):
        if self.run_id is None:
            self.skipTest("no run with candidates")
        resp = self.client.get(f"{PREFIX}/clustering/runs/{self.run_id}/candidates")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["run_id"], self.run_id)
        self.assertGreater(len(body["candidates"]), 0)
        self.assertIn("candidate_k", body["candidates"][0])

    def test_get_candidates_unknown_run_404(self):
        resp = self.client.get(f"{PREFIX}/clustering/runs/999999/candidates")
        self.assertEqual(resp.status_code, 404)

    # ── reports ───────────────────────────────────────────
    def test_report_creates_correct_job(self):
        resp = self._track(
            self.client.post(f"{PREFIX}/reports", json={"report_names": ["application_trend"]})
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "report_generate")
        self.assertEqual(body["payload"]["report_names"], ["application_trend"])

    def test_report_omitted_names_uses_default_reports(self):
        """確認 API 省略 report_names 時寫入完整預設報表名單。"""
        resp = self._track(self.client.post(f"{PREFIX}/reports", json={}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["payload"]["report_names"], list(DEFAULT_REPORT_NAMES))

    def test_report_null_names_uses_default_reports(self):
        """確認 API 傳入 null report_names 時寫入完整預設報表名單。"""
        resp = self._track(self.client.post(f"{PREFIX}/reports", json={"report_names": None}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["payload"]["report_names"], list(DEFAULT_REPORT_NAMES))

    def test_report_empty_names_uses_default_reports(self):
        """確認 API 傳入空 report_names 時寫入完整預設報表名單。"""
        resp = self._track(self.client.post(f"{PREFIX}/reports", json={"report_names": []}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["payload"]["report_names"], list(DEFAULT_REPORT_NAMES))

    def test_report_unknown_name_422(self):
        resp = self.client.post(f"{PREFIX}/reports", json={"report_names": ["no_such_report"]})
        self.assertEqual(resp.status_code, 422)

    def test_report_bad_filter_column_422(self):
        resp = self.client.post(
            f"{PREFIX}/reports",
            json={"report_names": ["application_trend"], "filters": {"not_a_column": 1}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_report_filter_not_supported_by_report_422(self):
        resp = self.client.post(
            f"{PREFIX}/reports",
            json={"report_names": ["application_trend"], "filters": {"publication_year": 2024}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_report_roundtrip(self):
        created = self._track(
            self.client.post(f"{PREFIX}/reports", json={"report_names": ["application_trend"]})
        ).json()
        resp = self.client.get(f"{PREFIX}/reports/{created['job_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_type"], "report_generate")

    def test_get_report_wrong_type_404(self):
        # 用一個 clustering job 的 id 查 /reports 應 404
        calib = self._track(
            self.client.post(
                f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate",
                json={"source_field": "wips_independent_claims"},
            )
        ).json()
        resp = self.client.get(f"{PREFIX}/reports/{calib['job_id']}")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
