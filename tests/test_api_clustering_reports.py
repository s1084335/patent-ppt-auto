"""E3 API 測試：clustering／reports 端點只驗「建對 job」與輸入驗證、讀候選。

實際執行歸 worker，這裡不跑分群/報表。建立的 job 以回傳 job_id 追蹤，
結尾逐一刪除。需要 DB（連不到就 skip）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from backend.app.main import app


PREFIX = "/api/v1"
client = TestClient(app)


def _connect():
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs())


class ClusteringReportsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from backend.app.db.connection import get_connection_kwargs

        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        try:
            with psycopg.connect(**get_connection_kwargs(), connect_timeout=3) as conn:
                ws = conn.execute(
                    "SELECT workspace_id FROM app_layer.workspaces ORDER BY workspace_id LIMIT 1"
                ).fetchone()
                run = conn.execute(
                    "SELECT run_id FROM derived_layer.topic_candidates "
                    "GROUP BY run_id ORDER BY run_id LIMIT 1"
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"DB unreachable: {exc}")
        if ws is None:
            raise unittest.SkipTest("no workspace to test against")
        cls.ws_id = int(ws[0])
        cls.run_id = int(run[0]) if run else None

    def setUp(self):
        self._created: list[int] = []

    def tearDown(self):
        if self._created:
            with _connect() as conn:
                conn.execute(
                    "DELETE FROM app_layer.processing_jobs WHERE job_id = ANY(%s)",
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
            client.post(
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
        resp = client.post(
            f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate",
            json={"source_field": "technical"},  # 非白名單
        )
        self.assertEqual(resp.status_code, 422)

    def test_calibrate_unknown_workspace_404(self):
        resp = client.post(
            f"{PREFIX}/workspaces/999999/clustering/calibrate",
            json={"source_field": "effect_summary"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_calibrate_idempotency(self):
        body = {"source_field": "effect_summary", "idempotency_key": "_verify_e3_calib"}
        a = self._track(client.post(f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate", json=body))
        b = self._track(client.post(f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate", json=body))
        self.assertEqual(a.json()["job_id"], b.json()["job_id"])

    # ── incremental ───────────────────────────────────────
    def test_incremental_creates_correct_job(self):
        resp = self._track(
            client.post(
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
            client.post(
                f"{PREFIX}/clustering/runs/{self.run_id}/finalize",
                json={"candidate_id": 1, "selected_by": "tester"},
            )
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "clustering_finalize")
        self.assertEqual(body["payload"]["run_id"], self.run_id)
        self.assertEqual(body["payload"]["candidate_id"], 1)

    def test_finalize_unknown_run_404(self):
        resp = client.post(
            f"{PREFIX}/clustering/runs/999999/finalize",
            json={"candidate_id": 1},
        )
        self.assertEqual(resp.status_code, 404)

    # ── candidates 讀取 ───────────────────────────────────
    def test_get_candidates(self):
        if self.run_id is None:
            self.skipTest("no run with candidates")
        resp = client.get(f"{PREFIX}/clustering/runs/{self.run_id}/candidates")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["run_id"], self.run_id)
        self.assertGreater(len(body["candidates"]), 0)
        self.assertIn("candidate_k", body["candidates"][0])

    def test_get_candidates_unknown_run_404(self):
        resp = client.get(f"{PREFIX}/clustering/runs/999999/candidates")
        self.assertEqual(resp.status_code, 404)

    # ── reports ───────────────────────────────────────────
    def test_report_creates_correct_job(self):
        resp = self._track(
            client.post(f"{PREFIX}/reports", json={"report_names": ["application_trend"]})
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_type"], "report_generate")
        self.assertEqual(body["payload"]["report_names"], ["application_trend"])

    def test_report_unknown_name_422(self):
        resp = client.post(f"{PREFIX}/reports", json={"report_names": ["no_such_report"]})
        self.assertEqual(resp.status_code, 422)

    def test_report_bad_filter_column_422(self):
        resp = client.post(
            f"{PREFIX}/reports",
            json={"report_names": ["application_trend"], "filters": {"not_a_column": 1}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_report_roundtrip(self):
        created = self._track(
            client.post(f"{PREFIX}/reports", json={"report_names": ["application_trend"]})
        ).json()
        resp = client.get(f"{PREFIX}/reports/{created['job_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_type"], "report_generate")

    def test_get_report_wrong_type_404(self):
        # 用一個 clustering job 的 id 查 /reports 應 404
        calib = self._track(
            client.post(
                f"{PREFIX}/workspaces/{self.ws_id}/clustering/calibrate",
                json={"source_field": "wips_independent_claims"},
            )
        ).json()
        resp = client.get(f"{PREFIX}/reports/{calib['job_id']}")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
