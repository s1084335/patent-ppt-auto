"""wips_importer.update_patent_empty_fields 的 regression 契約（拋棄式 DB patent_ppt_impfix）。

Regression 背景（2026-07-21）：guard 以 `%(param)s IS NOT NULL` 出現在無型別語境，
psycopg3 server-side binding 下 PostgreSQL 拋 AmbiguousParameter（could not determine
data type of parameter），整個匯入炸掉。本測試直接對真 PG 呼叫該函式重現。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_impfix"
BASE_REV = "0021_derived_app_consolidation"  # HEAD（含 0019/0020/0021 schema 變更）
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


class UpdatePatentEmptyFieldsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("PGHOST", "PGDATABASE", "DATABASE_URL")}
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
        command.upgrade(cfg, BASE_REV)
        with psycopg.connect(**_kw(TEST_DB)) as c:
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (900001, '既有標題')")
            c.commit()

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
        except Exception:  # noqa: BLE001
            pass

    def _params(self, **overrides):
        """依 _UPDATE_COLUMN_PARAMS 組全 None 參數（與 SET 子句同名），再套 overrides。"""
        from backend.app.importers.wips_importer import _UPDATE_COLUMN_PARAMS

        params = {param: None for _, param in _UPDATE_COLUMN_PARAMS}
        params.update(overrides)
        return params

    def test_fills_only_null_fields_without_ambiguous_parameter(self):
        """補空欄不得拋 AmbiguousParameter；只補 NULL 欄、不覆蓋既有值；no-op 回 False。"""
        from backend.app.importers.wips_importer import update_patent_empty_fields

        with psycopg.connect(**_kw(TEST_DB), options="-c search_path=core_layer,raw_layer,public") as c:
            cur = c.cursor()
            # Red 重現點：修正前這行拋 psycopg.errors.AmbiguousParameter
            changed = update_patent_empty_fields(
                cur, 900001, self._params(all_claims="1. 一種健身器材……", title="新標題不得蓋舊值"))
            c.commit()
            self.assertTrue(changed, "有 NULL 欄可補時應回 True")
            row = c.execute(
                'SELECT title, "所有權利要求[JP,KR,CN]" FROM core_layer.patents WHERE id=900001'
            ).fetchone()
        self.assertEqual(row[0], "既有標題", "既有非空值不得被覆蓋")
        self.assertEqual(row[1], "1. 一種健身器材……", "NULL 欄應補入新值")

    def test_noop_returns_false(self):
        """全部參數 None（無可補）時 UPDATE 不命中，回 False。"""
        from backend.app.importers.wips_importer import update_patent_empty_fields

        with psycopg.connect(**_kw(TEST_DB), options="-c search_path=core_layer,raw_layer,public") as c:
            cur = c.cursor()
            self.assertFalse(update_patent_empty_fields(cur, 900001, self._params()))
            c.commit()


if __name__ == "__main__":
    unittest.main()
