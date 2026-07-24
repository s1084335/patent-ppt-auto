"""0034 市場資料線底層兩表契約測試（獨立測試 DB，不碰 patent_ppt）。

db patent_ppt_mktdoc → upgrade head，驗證：
- derived_layer.market_documents（PDF metadata，內容在檔案系統不在 DB）欄位定版、
  FK ON DELETE CASCADE、byte_size 有存（因內容不在 DB，無法 length() 推導）。
- derived_layer.market_doc_summaries（AI 摘要版本）欄位定版、status 兩態 CHECK、
  payload_json 數值欄可空（整欄可 NULL）、accepted_at 可空、FK CASCADE、多版本並存。
- downgrade 可移除兩表。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config


TEST_DB = "patent_ppt_mktdoc"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# market_documents：PDF metadata，內容落檔案系統（MARKET_DOC_ROOT），DB 只存 metadata。
# 存 byte_size：內容不在 DB，無法用 length(content) 推導（與 workspace_documents 相反）。
EXPECTED_DOC_COLUMNS = {
    "document_id",
    "workspace_id",
    "original_filename",
    "stored_filename",
    "file_hash",
    "byte_size",
    "uploaded_at",
}

# market_doc_summaries：AI 摘要版本化。數值欄在 payload_json 內、整欄可 NULL（質性描述承接）。
EXPECTED_SUMMARY_COLUMNS = {
    "summary_id",
    "workspace_id",
    "version",
    "status",
    "payload_json",
    "narrative",
    "source_document",
    "accepted_at",
    "created_at",
}


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


class MarketDocMigrationTests(unittest.TestCase):
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

    def _new_workspace(self, conn, name: str) -> int:
        return conn.execute(
            "INSERT INTO app_layer.workspaces (workspace_name) VALUES (%s) RETURNING workspace_id",
            (name,),
        ).fetchone()[0]

    # ── market_documents ────────────────────────────────────
    def test_doc_columns(self):
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='market_documents'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertEqual(set(cols), EXPECTED_DOC_COLUMNS)
        self.assertEqual(cols["uploaded_at"], "timestamp with time zone")

    def test_doc_fk_cascade(self):
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-doc-cascade")
            c.execute(
                "INSERT INTO derived_layer.market_documents "
                "(workspace_id, original_filename, stored_filename, file_hash, byte_size) "
                "VALUES (%s, %s, %s, %s, %s)",
                (ws, "m.pdf", "ws-stored.pdf", "abc", 10),
            )
            c.commit()
            c.execute("DELETE FROM app_layer.workspaces WHERE workspace_id = %s", (ws,))
            c.commit()
            left = c.execute(
                "SELECT count(*) FROM derived_layer.market_documents WHERE workspace_id = %s",
                (ws,),
            ).fetchone()[0]
        self.assertEqual(left, 0)

    # ── market_doc_summaries ────────────────────────────────
    def test_summary_columns(self):
        with psycopg.connect(**_kw(TEST_DB)) as c:
            rows = c.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='derived_layer' AND table_name='market_doc_summaries'"
            ).fetchall()
        cols = {r[0]: r[1] for r in rows}
        self.assertEqual(set(cols), EXPECTED_SUMMARY_COLUMNS)
        self.assertEqual(cols["payload_json"], "jsonb")
        self.assertEqual(cols["accepted_at"], "timestamp with time zone")

    def test_summary_status_two_states_only(self):
        """status 只允許 current／superseded；其他值被 CHECK 擋。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-status")
            for st in ("current", "superseded"):
                c.execute(
                    "INSERT INTO derived_layer.market_doc_summaries "
                    "(workspace_id, version, status) VALUES (%s, %s, %s)",
                    (ws, 1, st),
                )
            c.commit()
            with self.assertRaises(psycopg.errors.CheckViolation):
                c.execute(
                    "INSERT INTO derived_layer.market_doc_summaries "
                    "(workspace_id, version, status) VALUES (%s, %s, %s)",
                    (ws, 2, "archived"),
                )
            c.rollback()

    def test_summary_nullable_payload_and_accepted(self):
        """數值欄整包 payload_json 可為 NULL、accepted_at 可為 NULL（未確認）、narrative 可空。

        規格鐵律：市場資料是輔助，數值欄必須可空、空值由質性描述承接。
        """
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-null")
            sid = c.execute(
                "INSERT INTO derived_layer.market_doc_summaries "
                "(workspace_id, version, status, narrative) VALUES (%s, %s, %s, %s) "
                "RETURNING summary_id",
                (ws, 1, "current", "北美為主要市場，通路以家居賣場為主"),
            ).fetchone()[0]
            c.commit()
            row = c.execute(
                "SELECT payload_json, accepted_at, narrative FROM derived_layer.market_doc_summaries "
                "WHERE summary_id = %s",
                (sid,),
            ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertEqual(row[2], "北美為主要市場，通路以家居賣場為主")

    def test_summary_multiple_versions_coexist(self):
        """同一 workspace 多版本並存（重跑產生新版本，舊版標 superseded）。"""
        with psycopg.connect(**_kw(TEST_DB)) as c:
            ws = self._new_workspace(c, "ws-versions")
            c.execute(
                "INSERT INTO derived_layer.market_doc_summaries "
                "(workspace_id, version, status) VALUES (%s, %s, %s)",
                (ws, 1, "superseded"),
            )
            c.execute(
                "INSERT INTO derived_layer.market_doc_summaries "
                "(workspace_id, version, status) VALUES (%s, %s, %s)",
                (ws, 2, "current"),
            )
            c.commit()
            count = c.execute(
                "SELECT count(*) FROM derived_layer.market_doc_summaries WHERE workspace_id = %s",
                (ws,),
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_downgrade_removes_tables(self):
        """downgrade 到 0033 可移除兩表；再 upgrade 回 head 不影響其他測試。"""
        cfg = _alembic_cfg()
        command.downgrade(cfg, "0033_company_alias_ai_suggested")
        with psycopg.connect(**_kw(TEST_DB)) as c:
            doc = c.execute("SELECT to_regclass('derived_layer.market_documents')").fetchone()[0]
            summ = c.execute(
                "SELECT to_regclass('derived_layer.market_doc_summaries')"
            ).fetchone()[0]
        self.assertIsNone(doc)
        self.assertIsNone(summ)
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
