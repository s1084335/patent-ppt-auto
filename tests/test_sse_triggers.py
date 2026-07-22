"""0022 SSE notify triggers 契約測試（獨立測試 DB，不碰 patent_ppt）。

db patent_ppt_ssetrig → upgrade 到 0022，驗證：
- UPDATE workflow_runs（status 或 progress_percent 變）→ pg_notify('patent_events')
- INSERT workflow_outputs → pg_notify('patent_events')
- downgrade 移除 trigger
- 不相干 UPDATE 不推播

psycopg v3 Connection.notifies() 是 blocking generator，改用 async 版的
asyncio.wait_for + __anext__() 做 timeout notify 接收。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_ssetrig"
HEAD_BEFORE_SSE = "0021_derived_app_consolidation"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        dbname=dbname,
    )
    pwd = os.getenv("PGPASSWORD")
    if pwd:
        kw["password"] = pwd
    return kw


def _alembic_cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


class SseTriggerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev_pghost = os.environ.get("PGHOST")
        os.environ["PGHOST"] = "127.0.0.1"
        cls._prev_pgdb = os.environ.get("PGDATABASE")
        cls._prev_dburl = os.environ.get("DATABASE_URL")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB

        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")

        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")

    @classmethod
    def tearDownClass(cls):
        if cls._prev_pghost is None:
            os.environ.pop("PGHOST", None)
        else:
            os.environ["PGHOST"] = cls._prev_pghost
        if cls._prev_pgdb is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = cls._prev_pgdb
        if cls._prev_dburl is not None:
            os.environ["DATABASE_URL"] = cls._prev_dburl
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def _seed_run(self) -> int:
        with psycopg.connect(**_kw(TEST_DB)) as c:
            row = c.execute(
                "INSERT INTO app_layer.workflow_runs "
                "(run_type, request_json, worker_state_json) "
                "VALUES ('test:probe', '{}'::jsonb, "
                "'{\"progress_percent\":0,\"current_stage\":\"init\"}'::jsonb) "
                "RETURNING run_id"
            ).fetchone()
            c.commit()
        return int(row[0])

    def _trigger_and_collect(self, action_sql: str, params: tuple,
                             channel: str = "patent_events") -> list[dict]:
        """用 async psycopg LISTEN，執行 action，await notify 回傳 payload list。

        Windows 需用 SelectorEventLoop（ProactorEventLoop 與 psycopg async 不相容）。
        """
        import selectors
        results: list[dict] = []

        async def run() -> None:
            conn = await psycopg.AsyncConnection.connect(**_kw(TEST_DB))
            try:
                await conn.execute(f"LISTEN {channel}")
                await conn.commit()
                with psycopg.connect(**_kw(TEST_DB)) as act_conn:
                    act_conn.execute(action_sql, params)
                    act_conn.commit()
                gen = conn.notifies()
                try:
                    notify = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
                    results.append(json.loads(notify.payload))
                except asyncio.TimeoutError:
                    pass
            finally:
                await conn.close()

        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()
        return results

    # ── 測試 ──

    def test_workflow_runs_update_status_fires_notify(self):
        """UPDATE workflow_runs.status → pg_notify('patent_events', {kind:'run',...})。"""
        run_id = self._seed_run()
        payloads = self._trigger_and_collect(
            "UPDATE app_layer.workflow_runs SET status = 'running' WHERE run_id = %s",
            (run_id,))
        self.assertEqual(len(payloads), 1, "應收到 1 個 notify")
        p = payloads[0]
        self.assertEqual(p["kind"], "run")
        self.assertEqual(p["run_id"], run_id)
        self.assertEqual(p["status"], "running")

    def test_workflow_runs_update_progress_fires_notify(self):
        """UPDATE progress_percent → trigger 推播。"""
        run_id = self._seed_run()
        payloads = self._trigger_and_collect(
            "UPDATE app_layer.workflow_runs SET worker_state_json = "
            "worker_state_json || '{\"progress_percent\":42,\"current_stage\":\"proc\"}'::jsonb "
            "WHERE run_id = %s",
            (run_id,))
        self.assertGreaterEqual(len(payloads), 1)
        p = payloads[-1]
        self.assertEqual(p["kind"], "run")
        self.assertEqual(p["progress"], 42)
        self.assertEqual(p["stage"], "proc")

    def test_workflow_outputs_insert_fires_notify(self):
        """INSERT workflow_outputs → notify。"""
        run_id = self._seed_run()
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute(
                "UPDATE app_layer.workflow_runs SET status = 'running' WHERE run_id = %s",
                (run_id,))
            c.commit()
        payloads = self._trigger_and_collect(
            "INSERT INTO app_layer.workflow_outputs "
            "(run_id, output_type, version, data_json) "
            "VALUES (%s, 'test:out', 1, '{}'::jsonb)",
            (run_id,))
        self.assertEqual(len(payloads), 1)
        p = payloads[0]
        self.assertEqual(p["kind"], "output")
        self.assertEqual(p["run_id"], run_id)
        self.assertEqual(p["output_type"], "test:out")
        self.assertEqual(p["version"], 1)

    def test_downgrade_removes_triggers(self):
        """downgrade 0022→0021 後 UPDATE 不再推播。"""
        cfg = _alembic_cfg()
        try:
            command.downgrade(cfg, HEAD_BEFORE_SSE)
            run_id = self._seed_run()
            payloads = self._trigger_and_collect(
                "UPDATE app_layer.workflow_runs SET status = 'running' WHERE run_id = %s",
                (run_id,))
            self.assertEqual(len(payloads), 0, "downgrade 後不應收到 notify")
        finally:
            command.upgrade(cfg, "head")

    def test_no_notify_on_unrelated_column_update(self):
        """不相干欄位（run_type）UPDATE 不觸發 notify。"""
        run_id = self._seed_run()
        payloads = self._trigger_and_collect(
            "UPDATE app_layer.workflow_runs SET run_type = 'test:irrelevant' WHERE run_id = %s",
            (run_id,))
        self.assertEqual(len(payloads), 0, "不相干 UPDATE 不應觸發 notify")


if __name__ == "__main__":
    unittest.main()
