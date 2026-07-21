"""wips_importer post-0019/0021 全流程契約（拋棄式 DB patent_ppt_importtest）。

驗證：dry-run 不寫、實匯 407 件「所有權利要求」入庫、raw_records 來源 metadata
（source_system/source_file_hash/imported_at）正確、整檔重匯冪等跳過。
不寫 source_files/dedupe_key（表/欄已於 0019/0020 移除）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_importtest"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
XLSX = PROJECT_ROOT / "data" / "raw" / "TextDown_20260721_pm121347_407.xlsx"
_prev_env: dict[str, str | None] = {}


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.getenv("PGPASSWORD")
    return kw


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
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def tearDownModule():
    for k, v in _prev_env.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        with psycopg.connect(**_kw("postgres"), autocommit=True, connect_timeout=3) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    except Exception:  # noqa: BLE001
        pass


def _scalar(sql, params=()):
    with psycopg.connect(**_kw(TEST_DB)) as c:
        row = c.execute(sql, params).fetchone()
    return row[0] if row else None


@unittest.skipUnless(XLSX.exists(), "缺 TextDown 407 xlsx")
class ImportFlowTests(unittest.TestCase):
    def test_a_dry_run_writes_nothing(self):
        from backend.app.importers.wips_importer import import_wips_file
        summary = import_wips_file(XLSX, dry_run=True)
        self.assertEqual(summary["records"], 407)
        self.assertNotIn("status", summary)  # dry-run 不進 DB 段
        self.assertEqual(_scalar("SELECT count(*) FROM core_layer.patents"), 0)

    def test_b_real_import_and_claims_and_raw_metadata(self):
        from backend.app.importers.wips_importer import import_wips_file
        summary = import_wips_file(XLSX, dry_run=False)
        self.assertEqual(summary["status"], "imported")
        self.assertEqual(summary["inserted"] + summary["matched_existing"], 407)
        # 407 件全有「所有權利要求」
        self.assertEqual(_scalar(
            'SELECT count(*) FROM core_layer.patents '
            'WHERE "所有權利要求[JP,KR,CN]" IS NOT NULL AND btrim("所有權利要求[JP,KR,CN]") <> %s', ("",)),
            407)
        # raw_records 來源 metadata 正確（407 列、同一 hash、source_system/imported_at 皆非空）
        self.assertEqual(_scalar("SELECT count(*) FROM raw_layer.raw_records"), 407)
        self.assertEqual(_scalar("SELECT count(DISTINCT source_file_hash) FROM raw_layer.raw_records"), 1)
        self.assertEqual(_scalar(
            "SELECT count(*) FROM raw_layer.raw_records WHERE source_system IS NULL OR imported_at IS NULL"), 0)
        self.assertIn("alias_variants", summary)  # 變體註冊接線有跑

    def test_c_reimport_same_file_is_idempotent(self):
        from backend.app.importers.wips_importer import import_wips_file
        before = _scalar("SELECT count(*) FROM core_layer.patents")
        summary = import_wips_file(XLSX, dry_run=False)
        self.assertEqual(summary["status"], "skipped_duplicate_file")
        self.assertEqual(_scalar("SELECT count(*) FROM core_layer.patents"), before)  # 未重寫


if __name__ == "__main__":
    unittest.main()
