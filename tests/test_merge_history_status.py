"""合併歷史必須反映 job 真實狀態（2026-07-27 實機發現）。

實機症狀：使用者按「合併兩主題」→ `topic_merge #97` 永遠 queued（沒 worker 領）→
兩個主題原封不動，但「合併歷史」照樣顯示 `97 | T004, T002 → T004`＋「解除合併」鈕。
畫面說合併好了、還能解除，實際上什麼都沒發生。

根因：`list_merge_history` 撈 `run_type='topic_merge'` 的所有 run，**完全不看 status**。
原程式註解甚至明寫「worker 尚未執行，以來源首鍵為結果主題顯示」——知道可能沒執行，
仍照樣列進歷史。

「歷史」的語意是**已經發生的事**。未完成的 job 不該進歷史；但也不能直接隱藏
（使用者按了要看得到回饋），故：
- succeeded → 列入歷史，可解除合併。
- queued／running → 列出但標示「處理中」，**不可**解除合併（還沒合併，解什麼）。
- failed → 列出並標示失敗原因，不可解除合併。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_mergehist"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_FIELD = "wips_independent_claims"


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


class MergeHistoryStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        command.upgrade(_alembic_cfg(), "head")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def setUp(self):
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            c.execute("DELETE FROM app_layer.workflow_runs")
            c.execute("DELETE FROM app_layer.workspaces")
            self.ws = c.execute(
                "INSERT INTO app_layer.workspaces (workspace_name, patent_ids_json) "
                "VALUES ('ws-merge', '[]') RETURNING workspace_id").fetchone()[0]

    def _merge_run(self, conn, status: str, topic_keys: list[str]) -> int:
        return conn.execute(
            "INSERT INTO app_layer.workflow_runs (workspace_id, run_type, status, request_json) "
            "VALUES (%s, 'topic_merge', %s, %s) RETURNING run_id",
            (self.ws, status,
             json.dumps({"source_field": SOURCE_FIELD, "topic_keys": topic_keys})),
        ).fetchone()[0]

    def _history(self):
        from backend.app.repositories.postgres_topic_repository import (
            PostgresTopicRepository,
        )

        return PostgresTopicRepository().list_merge_history(self.ws, SOURCE_FIELD)

    def test_queued_merge_is_not_unmergeable(self):
        """queued 的合併尚未發生，不得提供「解除合併」。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            self._merge_run(c, "queued", ["T004", "T002"])
        items = self._history()
        self.assertEqual(len(items), 1, "仍要列出，讓使用者看到按過（不可靜默隱藏）")
        self.assertFalse(items[0]["can_unmerge"],
                         "還沒合併就提供解除合併，是假訊號")
        self.assertIsNotNone(items[0]["blocked_reason"])

    def test_running_merge_is_not_unmergeable(self):
        """running 同理——尚未完成。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            self._merge_run(c, "running", ["T001", "T003"])
        items = self._history()
        self.assertFalse(items[0]["can_unmerge"])

    def test_failed_merge_is_not_unmergeable(self):
        """failed 的合併沒有發生，同樣不可解除。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            self._merge_run(c, "failed", ["T001", "T005"])
        items = self._history()
        self.assertFalse(items[0]["can_unmerge"])

    def test_succeeded_merge_is_unmergeable(self):
        """succeeded 才是真的合併了，可解除。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            self._merge_run(c, "succeeded", ["T001", "T002"])
        items = self._history()
        self.assertTrue(items[0]["can_unmerge"])
        self.assertIsNone(items[0]["blocked_reason"])

    def test_history_exposes_status(self):
        """每筆要帶 status，前端才能顯示「處理中／失敗」而非一律當成已完成。"""
        with psycopg.connect(**_kw(TEST_DB), autocommit=True) as c:
            self._merge_run(c, "queued", ["T004", "T002"])
            self._merge_run(c, "succeeded", ["T001", "T003"])
        items = sorted(self._history(), key=lambda i: i["merge_run_id"])
        self.assertEqual([i.get("status") for i in items], ["queued", "succeeded"])


if __name__ == "__main__":
    unittest.main()
