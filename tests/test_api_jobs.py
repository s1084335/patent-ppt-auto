"""E2 backend API 測試（0021 遷移版）：以 FastAPI TestClient 驅動 /health、/ready、
GET /jobs/{id}，對拋棄式 DB patent_ppt_apijobs 驗證（絕不碰 patent_ppt）。

/health 不需 DB；/ready 與 job 查詢需 DB（admin 不可用則整組 skip）。
0021 後佇列在 app_layer.workflow_runs；測試 job 以 request_json 標記 _verify 並清除。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.db import job_repository as jr
from backend.app.db.connection import get_database_url


PREFIX = "/api/v1"
VERIFY_KEY = "_verify_marker"
TEST_DB = "patent_ppt_apijobs"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    """組出連測試庫的 psycopg 參數（與 test_job_repository 同源）。"""
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    password = os.getenv("PGPASSWORD")
    if password:
        kw["password"] = password
    return kw


def _reset_pool():
    """關閉並清空 lazy 連線池單例，讓 get_pool() 依目前 env 重建（避免綁到別庫）。"""
    from backend.app.db import connection

    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:  # noqa: BLE001
            pass
        connection._pool = None


def setUpModule():
    """建拋棄式 DB → upgrade head；admin 不可用則整組 skip（含不需 DB 的 health）。"""
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"  # Windows localhost 走 IPv6 會慢
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB  # API 與 repository 走 get_connection_kwargs()/get_pool()
    _reset_pool()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def tearDownModule():
    _reset_pool()
    for k, v in _prev_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _cleanup():
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        conn.execute(
            "DELETE FROM app_layer.workflow_runs WHERE request_json ? %s",
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

    def test_job_dict_exposes_error_message_field(self):
        """job_to_dict 一律含 error_message 欄；未失敗時為 None（前端失敗卡讀此欄顯示原因）。"""
        job = jr.create_job("clustering_calibrate", {VERIFY_KEY: True})
        resp = client.get(f"{PREFIX}/jobs/{job.job_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("error_message", body)
        self.assertIsNone(body["error_message"])

    def test_failed_job_exposes_error_message(self):
        """失敗 job 經 worker fail_job 落 error_message，GET /jobs 讀回同一訊息（走既有 repository）。"""
        job = jr.create_job("clustering_calibrate", {VERIFY_KEY: True})
        worker = jr.WorkerQueueClient()
        claimed = worker.claim_next_job(worker_id="verify-worker")
        self.assertIsNotNone(claimed)
        worker.fail_job(
            job_id=claimed.job_id,
            worker_id="verify-worker",
            error_message="boom: something broke",
        )
        resp = client.get(f"{PREFIX}/jobs/{claimed.job_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error_message"], "boom: something broke")
        self.assertEqual(body["current_stage"], "failed")

    def test_get_job_reads_result_from_workflow_outputs(self):
        """GET /jobs/{id} 要把 workflow_outputs 最新結果併回 result。

        0021 後 queue row 本身不存 result_json；前端匯入完成卡片讀 /jobs/{id}.result，
        因此 API 必須在單筆查詢時補讀 job_result:{run_type}。
        """
        job = jr.create_job("patent_import", {VERIFY_KEY: True})
        worker = jr.WorkerQueueClient()
        claimed = worker.claim_next_job(worker_id="verify-worker")
        self.assertIsNotNone(claimed)
        result = {
            "inserted": 2,
            "matched_existing": 3,
            "updated": 1,
            "patent_ids": [101, 102, 103, 104, 105],
        }
        worker.complete_job(job_id=claimed.job_id, worker_id="verify-worker", result_json=result)

        resp = client.get(f"{PREFIX}/jobs/{claimed.job_id}")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["result"], result)

    def test_get_job_not_found(self):
        resp = client.get(f"{PREFIX}/jobs/999999999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
