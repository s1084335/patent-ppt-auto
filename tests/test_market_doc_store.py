"""市場資料線底層 store CRUD 行為（拋棄式 DB，絕不碰 patent_ppt）。

覆蓋：
- MarketDocSummaryStore：建摘要（數值欄可空）、查最新現行版、重跑標舊版過期只留一個 current、
  逐筆 accept 落款 accepted_at。
- MarketDocumentStore：記 PDF metadata（內容在檔案系統不在 DB）、依 workspace 列出、單筆取、刪除。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_mktdoc_store"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


def _cfg() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def setUpModule():
    global _prev_env
    _prev_env = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
    os.environ["PGHOST"] = "127.0.0.1"
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"admin DB unavailable: {exc}")
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGDATABASE"] = TEST_DB
    command.upgrade(_cfg(), "head")


def tearDownModule():
    # 關掉可能已開的連線池，避免 DROP DATABASE 被占用。
    try:
        from backend.app.db import connection

        if getattr(connection, "_pool", None) is not None:
            connection._pool.close()
            connection._pool = None
    except Exception:  # noqa: BLE001
        pass
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _new_workspace(name: str) -> int:
    with psycopg.connect(**_kw(TEST_DB)) as c:
        wid = c.execute(
            "INSERT INTO app_layer.workspaces (workspace_name) VALUES (%s) RETURNING workspace_id",
            (name,),
        ).fetchone()[0]
        c.commit()
    return int(wid)


class MarketDocSummaryStoreTests(unittest.TestCase):
    def setUp(self):
        from backend.app.market import market_doc_store

        self.store = market_doc_store.MarketDocSummaryStore()

    def test_create_and_get_current(self):
        """建摘要（含數值 payload）→ 查最新現行版拿得到，status=current、accepted_at 未落款。"""
        ws = _new_workspace("ws-create")
        sid = self.store.create_summary(
            ws,
            payload_json={"metrics": [{"label": "全球市場", "value_min": 55, "value_max": 110}]},
            narrative="步進式為北美住宅產品",
            source_document="global-mower.pdf",
        )
        current = self.store.get_current(ws)
        self.assertEqual(current["summary_id"], sid)
        self.assertEqual(current["version"], 1)
        self.assertEqual(current["status"], "current")
        self.assertIsNone(current["accepted_at"])
        self.assertEqual(current["payload_json"]["metrics"][0]["value_min"], 55)

    def test_create_allows_null_numeric(self):
        """數值欄可空：payload_json 可 None，質性 narrative 承接（規格鐵律）。"""
        ws = _new_workspace("ws-null")
        sid = self.store.create_summary(
            ws,
            payload_json=None,
            narrative="北美為主要市場，通路以家居賣場為主，CARB 法規推動電動化",
            source_document="qualitative.pdf",
        )
        current = self.store.get_current(ws)
        self.assertEqual(current["summary_id"], sid)
        self.assertIsNone(current["payload_json"])
        self.assertIn("CARB", current["narrative"])

    def test_rerun_supersedes_old_version(self):
        """重跑產生新版本、舊版標過期；get_current 只回最新現行版，version 遞增。"""
        ws = _new_workspace("ws-rerun")
        first = self.store.create_summary(ws, payload_json={"a": 1}, source_document="v1.pdf")
        second = self.store.create_summary(ws, payload_json={"a": 2}, source_document="v2.pdf")
        current = self.store.get_current(ws)
        self.assertEqual(current["summary_id"], second)
        self.assertEqual(current["version"], 2)
        # 舊版仍在但標 superseded，只保留一個 current。
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT summary_id, status FROM derived_layer.market_doc_summaries "
                "WHERE workspace_id = %s ORDER BY version",
                (ws,),
            ).fetchall()
        statuses = {r[0]: r[1] for r in rows}
        self.assertEqual(statuses[first], "superseded")
        self.assertEqual(statuses[second], "current")
        currents = [s for s in statuses.values() if s == "current"]
        self.assertEqual(len(currents), 1)

    def test_accept_sets_accepted_at(self):
        """逐筆 accept 落款 accepted_at（未確認為 NULL）。"""
        ws = _new_workspace("ws-accept")
        sid = self.store.create_summary(ws, payload_json={"a": 1}, source_document="v.pdf")
        self.store.accept(sid)
        current = self.store.get_current(ws)
        self.assertIsNotNone(current["accepted_at"])

    def test_get_current_none_when_empty(self):
        """無摘要時 get_current 回 None（降級：無市場資料整區隱藏）。"""
        ws = _new_workspace("ws-empty")
        self.assertIsNone(self.store.get_current(ws))

    def test_get_accepted_current_only_when_accepted(self):
        """報表只讀 accepted：現行版未確認時 get_accepted_current 回 None，確認後才回。"""
        ws = _new_workspace("ws-accepted-current")
        sid = self.store.create_summary(ws, payload_json={"a": 1}, source_document="v.pdf")
        # 未確認草稿：現行版存在但 get_accepted_current 拿不到（實體隔離）。
        self.assertIsNone(self.store.get_accepted_current(ws))
        self.store.accept(sid)
        got = self.store.get_accepted_current(ws)
        self.assertIsNotNone(got)
        self.assertEqual(got["summary_id"], sid)

    def test_get_accepted_current_ignores_superseded(self):
        """舊版即使曾確認，被新版取代後 get_accepted_current 不回它（只讀現行版）。"""
        ws = _new_workspace("ws-accepted-superseded")
        first = self.store.create_summary(ws, payload_json={"a": 1}, source_document="v1.pdf")
        self.store.accept(first)
        # 重產：新現行版未確認 → 報表拿不到（舊版已 superseded，也不回）。
        self.store.create_summary(ws, payload_json={"a": 2}, source_document="v2.pdf")
        self.assertIsNone(self.store.get_accepted_current(ws))


class MarketDocumentStoreTests(unittest.TestCase):
    def setUp(self):
        from backend.app.market import market_doc_store

        self.store = market_doc_store.MarketDocumentStore()

    def test_record_and_list(self):
        """記 metadata（內容在檔案系統）→ 依 workspace 列出，含 byte_size 與 stored_filename。"""
        ws = _new_workspace("ws-docs")
        did = self.store.record_document(
            ws,
            original_filename="report.pdf",
            stored_filename="ws-abc.pdf",
            file_hash="deadbeef",
            byte_size=2048,
        )
        docs = self.store.list_documents(ws)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["document_id"], did)
        self.assertEqual(docs[0]["original_filename"], "report.pdf")
        self.assertEqual(docs[0]["byte_size"], 2048)
        self.assertEqual(docs[0]["stored_filename"], "ws-abc.pdf")

    def test_multiple_documents(self):
        """同一 workspace 可多份並存（不寫死一份）。"""
        ws = _new_workspace("ws-multi-docs")
        for i in range(3):
            self.store.record_document(
                ws, original_filename=f"m{i}.pdf", stored_filename=f"s{i}.pdf",
                file_hash=f"h{i}", byte_size=10 + i,
            )
        self.assertEqual(len(self.store.list_documents(ws)), 3)

    def test_get_and_delete(self):
        """單筆取回 stored_filename（供刪檔）、刪除回 True，再刪回 False。"""
        ws = _new_workspace("ws-del")
        did = self.store.record_document(
            ws, original_filename="x.pdf", stored_filename="sx.pdf",
            file_hash="h", byte_size=1,
        )
        got = self.store.get_document(ws, did)
        self.assertEqual(got["stored_filename"], "sx.pdf")
        self.assertTrue(self.store.delete_document(did, workspace_id=ws))
        self.assertFalse(self.store.delete_document(did, workspace_id=ws))


if __name__ == "__main__":
    unittest.main()
