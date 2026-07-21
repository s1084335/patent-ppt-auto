"""wips_importer 0019/0021 schema 合規：source_files/dedupe_key 不寫、raw_records 來源 metadata 正確。

所有 importer 內部函式使用不帶 schema 的裸表名，故連線需設 search_path。"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_impfix_0019"
HEAD_REV = "0021_derived_app_consolidation"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


def _rw(dbname: str) -> dict:
    """連線含 search_path 讓 importer 裸表名可用（raw_layer + core_layer + public）。"""
    kw = _kw(dbname)
    kw["options"] = "-c search_path=raw_layer,core_layer,public"
    return kw


class SchemaComplianceTests(unittest.TestCase):
    """驗證 post-0019/0021 importer 行為：不寫 source_files/dedupe_key、來源 metadata 正確。"""

    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
        os.environ["PGHOST"] = "127.0.0.1"
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        except Exception as exc:
            raise unittest.SkipTest(f"admin DB unavailable: {exc}")
        os.environ.pop("DATABASE_URL", None)
        os.environ["PGDATABASE"] = TEST_DB
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        command.upgrade(cfg, HEAD_REV)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        except Exception:
            pass

    def test_tables_have_post_0019_schema(self):
        """raw_records 無 source_file_id；patent_sources 無 source_file_id/dedupe_key。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            # raw_records 應有 source_system/source_file_hash/imported_at 且無 source_file_id
            rr_cols = {r[0] for r in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='raw_layer' AND table_name='raw_records'")}
            self.assertIn("source_system", rr_cols)
            self.assertIn("source_file_hash", rr_cols)
            self.assertIn("imported_at", rr_cols)
            self.assertNotIn("source_file_id", rr_cols)

            # source_files 表不存在
            src_files = c.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='raw_layer' AND table_name='source_files'").fetchone()[0]
            self.assertEqual(src_files, 0)

            # patent_sources 無 source_file_id 與 dedupe_key
            ps_cols = {r[0] for r in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='core_layer' AND table_name='patent_sources'")}
            self.assertNotIn("source_file_id", ps_cols)
            self.assertNotIn("dedupe_key", ps_cols)

    def test_source_metadata_in_raw_records(self):
        """匯入後 raw_records 有正確的 source_system/source_file_hash/imported_at。"""
        from backend.app.importers.wips_importer import (
            SOURCE_SYSTEM, insert_raw_record,
        )
        with psycopg.connect(**_rw(TEST_DB)) as c:
            with c.cursor() as cur:
                rid = insert_raw_record(cur, "Sheet1", {"申请号": "TW123", "_row_number": 1}, "abc123")
                row = c.execute(
                    "SELECT source_system, source_file_hash, imported_at FROM raw_layer.raw_records WHERE id=%s",
                    (rid,)).fetchone()
                c.execute("ROLLBACK")
        self.assertEqual(row[0], SOURCE_SYSTEM)
        self.assertEqual(row[1], "abc123")
        self.assertIsNotNone(row[2])

    def test_patent_source_no_extra_columns(self):
        """insert_patent_source 只寫 (patent_id, raw_record_id)，無 source_file_id。"""
        from backend.app.importers.wips_importer import insert_patent_source
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (990001, 'test')")
            c.execute("INSERT INTO raw_layer.raw_records (id, sheet_name, row_number, raw_data, source_system, source_file_hash, imported_at) "
                      "VALUES (990001, 'S', 1, '{}'::jsonb, 'WIPS', 'h', now())")
            with c.cursor() as cur:
                insert_patent_source(cur, 990001, 990001)
            row = c.execute(
                "SELECT patent_id, raw_record_id FROM core_layer.patent_sources WHERE patent_id=990001"
            ).fetchone()
            c.execute("ROLLBACK")
        self.assertEqual(row[0], 990001)
        self.assertEqual(row[1], 990001)

    def test_existing_non_null_not_overwritten(self):
        """既有專利非空值不被 importer COALESCE 覆蓋。"""
        from backend.app.importers.wips_importer import update_patent_empty_fields, _UPDATE_COLUMN_PARAMS
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute("INSERT INTO core_layer.patents (id, title, legal_status) VALUES (990002, '原標題', '有效')")
            params = {param: None for _, param in _UPDATE_COLUMN_PARAMS}
            params.update(title="新標題不得蓋舊值", legal_status="無效")
            with c.cursor() as cur:
                changed = update_patent_empty_fields(cur, 990002, params)
            row = c.execute("SELECT title, legal_status FROM core_layer.patents WHERE id=990002").fetchone()
            c.execute("ROLLBACK")
        self.assertFalse(changed, "無 NULL 欄可補時不得回傳 True")
        self.assertEqual(row[0], "原標題")
        self.assertEqual(row[1], "有效")

    def test_find_existing_raw_import_queries_raw_records(self):
        """find_existing_raw_import 正確查詢 raw_records（非 source_files）。"""
        from backend.app.importers.wips_importer import SOURCE_SYSTEM, find_existing_raw_import
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                "INSERT INTO raw_layer.raw_records (sheet_name, row_number, raw_data, source_system, source_file_hash, imported_at) "
                "VALUES ('S', 1, '{}'::jsonb, %s, 'known_hash', now())",
                (SOURCE_SYSTEM,))
            with c.cursor() as cur:
                self.assertTrue(find_existing_raw_import(cur, "known_hash"))
                self.assertFalse(find_existing_raw_import(cur, "nonexistent_hash"))
            c.execute("ROLLBACK")


if __name__ == "__main__":
    unittest.main()
