"""E2 backend API 測試：以 FastAPI TestClient 驅動 /health、/ready、GET /jobs/{id}。

/health 不需 DB；/ready 與 job 查詢需 DB（連不到就 skip）。測試 job 以
payload 標記 _verify 並清除。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.db import job_repository as jr
from backend.app.db.connection import get_database_url


PREFIX = "/api/v1"
VERIFY_KEY = "_verify_marker"
client = TestClient(app)


def _db_ok() -> bool:
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    try:
        with psycopg.connect(**get_connection_kwargs(), connect_timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


def _cleanup():
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        conn.execute(
            "DELETE FROM app_layer.processing_jobs WHERE payload_json ? %s",
            (VERIFY_KEY,),
        )
        conn.commit()


class HealthTests(unittest.TestCase):
    """health 是 liveness，不需 DB。"""

    def test_health_ok(self):
        resp = client.get(f"{PREFIX}/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class ReadyAndJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _db_ok():
            raise unittest.SkipTest("DB unreachable")
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_ready_reports_db_and_worker(self):
        resp = client.get(f"{PREFIX}/ready")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ready")
        self.assertTrue(body["database"]["ok"])
        self.assertIn("running_jobs", body["worker"])
        self.assertIn("healthy", body["worker"])

    def test_ready_bad_pgport_reports_database_not_ready(self):
        with patch.dict(os.environ, {"PGPORT": "not-an-int", "DATABASE_URL": ""}):
            resp = client.get(f"{PREFIX}/ready")
        self.assertEqual(resp.status_code, 503)
        detail = resp.json()["detail"]
        self.assertEqual(detail["status"], "not_ready")
        self.assertFalse(detail["database"]["ok"])
        self.assertIn("PGPORT must be an integer", detail["database"]["error"])

    def test_get_database_url_rejects_bad_pgport(self):
        with patch.dict(os.environ, {"PGPORT": "not-an-int", "DATABASE_URL": ""}):
            with self.assertRaisesRegex(ValueError, "PGPORT must be an integer"):
                get_database_url()

    def test_get_job_roundtrip(self):
        job = jr.create_job("clustering_calibrate", {VERIFY_KEY: True})
        resp = client.get(f"{PREFIX}/jobs/{job.job_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], job.job_id)
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["job_type"], "clustering_calibrate")
        self.assertEqual(body["progress_percent"], 0)

    def test_get_job_not_found(self):
        resp = client.get(f"{PREFIX}/jobs/999999999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
