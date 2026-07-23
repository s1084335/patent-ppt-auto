"""SSE events + tasks list 測試。

未接真 DB 時以 mock 測契約行為；接真 DB（patent_ppt_apievents 拋棄式）測整趟，
不碰 patent_ppt。

註：原本此檔另有 `POST /api/v1/ai-tasks` 的 enqueue 測試；該端點已與
`companion.py` 的建任務端點整併到 `backend/app/api/ai_tasks.py`（需 bearer token），
對應契約測試移至 `tests/test_api_ai_tasks.py`，此處只留唯讀列表與 SSE。
"""
from __future__ import annotations

import json
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


PREFIX = "/api/v1"
VERIFY_KEY = "_verify_marker_events"
TEST_DB = "patent_ppt_apievents"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
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
    from backend.app.db import connection
    if connection._pool is not None:
        try:
            connection._pool.close()
        except Exception:
            pass
        connection._pool = None


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
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
    except Exception:
        pass


def _cleanup():
    with psycopg.connect(**_kw(TEST_DB)) as conn:
        conn.execute(
            "DELETE FROM app_layer.workflow_runs WHERE request_json ? %s",
            (VERIFY_KEY,),
        )
        conn.commit()


class AiTaskEnqueueNoLongerUnauthenticatedTests(unittest.TestCase):
    """regression：建 AI 任務不得再有零認證入口。

    整併前 events.py 的 POST /ai-tasks 無任何認證，公網可達等同任何人可建任務。
    整併後同路徑由 ai_tasks.py 提供且掛 bearer token 依賴，未帶 token 不得成功建任務。
    """

    def test_enqueue_without_token_is_rejected(self):
        """未帶 token 呼叫 POST /ai-tasks 不得建立任務（401 或未設 token 的 503）。"""
        resp = client.post(
            f"{PREFIX}/ai-tasks",
            json={"task_type": "ai:narrative", "params": {"patent_ids": [1]}},
        )
        self.assertIn(resp.status_code, (401, 503))
        self.assertNotIn("run_id", resp.json())


class TasksListTests(unittest.TestCase):
    """GET /api/v1/tasks 列表。"""

    @classmethod
    def setUpClass(cls):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_tasks_list_returns_recent_jobs(self):
        """列出近期工作，含 run_id/type/status/progress/stage。"""
        # 先建兩筆
        jr.create_job("clustering_calibrate", {VERIFY_KEY: True})
        jr.create_job("report_generate", {VERIFY_KEY: True})
        resp = client.get(f"{PREFIX}/tasks?limit=20")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("tasks", body)
        self.assertIsInstance(body["tasks"], list)
        self.assertGreaterEqual(len(body["tasks"]), 2)
        for t in body["tasks"]:
            for k in ("job_id", "job_type", "status", "progress_percent", "current_stage"):
                self.assertIn(k, t)

    def test_tasks_list_default_limit(self):
        """未指定 limit 時預設 20。"""
        resp = client.get(f"{PREFIX}/tasks")
        self.assertEqual(resp.status_code, 200)
        # 不檢查長度，只檢查無 error

    def test_tasks_list_respects_limit(self):
        """limit=1 只回 1 筆。"""
        jr.create_job("clustering_calibrate", {VERIFY_KEY: True})
        jr.create_job("clustering_finalize", {VERIFY_KEY: True})
        resp = client.get(f"{PREFIX}/tasks?limit=1")
        self.assertEqual(resp.status_code, 200)
        tasks = resp.json()["tasks"]
        self.assertLessEqual(len(tasks), 1)


class SseEndpointTests(unittest.TestCase):
    """GET /api/v1/events SSE endpoint。

    TestClient 不支援 StreamingResponse（阻塞等待完整 body），因此 SSE 的 HTTP
    驗證只做 gated 模式──需外部 server 時才測。
    trigger 行為已在 test_sse_triggers.py 驗證。
    """

    def test_sse_gated_integration(self):
        """SSE HTTP 整合測試（gate：TEST_SERVER_ADDR 需設定）。"""
        addr = os.getenv("TEST_SERVER_ADDR")
        if not addr:
            self.skipTest("TEST_SERVER_ADDR 未設定，跳過 HTTP SSE 整合測試")
        import threading
        from urllib.request import Request, urlopen

        job = jr.create_job("clustering_calibrate", {VERIFY_KEY: True})
        received_events: list[str] = []

        def listen_sse():
            try:
                req = Request(f"http://{addr}{PREFIX}/events")
                resp = urlopen(req, timeout=5)
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data:"):
                        received_events.append(line[5:].strip())
                    if len(received_events) >= 1:
                        break
            except Exception:
                pass

        t = threading.Thread(target=listen_sse, daemon=True)
        t.start()
        import time
        time.sleep(0.3)
        with psycopg.connect(**_kw(TEST_DB)) as conn:
            conn.execute(
                "UPDATE app_layer.workflow_runs SET status = 'running' WHERE run_id = %s",
                (job.job_id,))
            conn.commit()
        t.join(timeout=5)
        self.assertGreaterEqual(len(received_events), 1)
        ev = json.loads(received_events[0])
        self.assertEqual(ev["kind"], "run")
        self.assertEqual(ev["run_id"], job.job_id)


if __name__ == "__main__":
    unittest.main()
