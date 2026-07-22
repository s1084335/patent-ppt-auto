"""wips_importer.update_patent_changed_fields 的 regression 契約（拋棄式 DB patent_ppt_impfix）。

政策沿革：
- 2026-07-21：guard 以 `%(param)s IS NOT NULL` 出現在無型別語境 → psycopg3 server-side
  binding 下 PostgreSQL 拋 AmbiguousParameter；改成 COALESCE(欄, p) 包住參數解掉。
- 2026-07-22（本檔涵蓋的最新政策）：取代舊「只補 NULL」語意——專利號命中既有時，逐欄
  比對，**新值非空（NULLIF(BTRIM(...),'') 判定）且與舊值不同就更新**；新值為空一律不覆蓋
  既有值（避免某批來源缺欄清空既有好資料）。函式改名 update_patent_changed_fields，
  stats["updated"] 語意變為「任一欄差異更新」。

本測試直接對真 PG 呼叫該函式驗證新語意（三 regression：狀態欄更新、新值空不清舊值、無差異不 updated）。
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


class UpdatePatentChangedFieldsTests(unittest.TestCase):
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

    def _conn(self):
        return psycopg.connect(**_kw(TEST_DB), options="-c search_path=core_layer,raw_layer,public")

    def test_fills_null_field_without_ambiguous_parameter(self):
        """補 NULL 欄不得拋 AmbiguousParameter；NULL 欄補入新值、既有非空值不同也一起更新。"""
        from backend.app.importers.wips_importer import update_patent_changed_fields

        with self._conn() as c:
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (900001, '既有標題')")
            cur = c.cursor()
            changed = update_patent_changed_fields(
                cur, 900001, self._params(all_claims="1. 一種健身器材……", title="新標題（差異即更新）"))
            self.assertTrue(changed, "有欄位可更新時應回 True")
            row = c.execute(
                'SELECT title, "所有權利要求[JP,KR,CN]" FROM core_layer.patents WHERE id=900001'
            ).fetchone()
            c.execute("ROLLBACK")
        # 新政策：既有非空值與新值不同 → 更新（不再保留舊值）。
        self.assertEqual(row[0], "新標題（差異即更新）", "非空新值與舊值不同應更新")
        self.assertEqual(row[1], "1. 一種健身器材……", "NULL 欄應補入新值")

    def test_status_column_updates_old_to_new(self):
        """regression①：狀態欄 legal_status 從舊值更新到新值（差異即更新）。"""
        from backend.app.importers.wips_importer import update_patent_changed_fields

        with self._conn() as c:
            c.execute(
                "INSERT INTO core_layer.patents (id, title, legal_status) VALUES (900002, '標題', '審查中')")
            cur = c.cursor()
            changed = update_patent_changed_fields(cur, 900002, self._params(legal_status="已核准"))
            self.assertTrue(changed, "狀態欄有差異時應回 True")
            row = c.execute("SELECT legal_status FROM core_layer.patents WHERE id=900002").fetchone()
            c.execute("ROLLBACK")
        self.assertEqual(row[0], "已核准", "狀態欄應由舊值更新到新值")

    def test_empty_new_value_does_not_clear_existing(self):
        """regression②：新值空（NULL/空字串）一律不覆蓋既有有值。"""
        from backend.app.importers.wips_importer import update_patent_changed_fields

        with self._conn() as c:
            c.execute(
                "INSERT INTO core_layer.patents (id, title, legal_status) VALUES (900003, '既有標題', '有效')")
            cur = c.cursor()
            # title 新值 NULL、legal_status 新值空白字串 → 皆不得覆蓋既有值；此呼叫無任何差異更新。
            changed = update_patent_changed_fields(
                cur, 900003, self._params(title=None, legal_status="   "))
            row = c.execute(
                "SELECT title, legal_status FROM core_layer.patents WHERE id=900003").fetchone()
            c.execute("ROLLBACK")
        self.assertFalse(changed, "新值皆空、無實際差異更新時應回 False")
        self.assertEqual(row[0], "既有標題", "新值 NULL 不得清空既有 title")
        self.assertEqual(row[1], "有效", "新值空字串不得清空既有 legal_status")

    def test_no_difference_not_updated(self):
        """regression③：新值與舊值完全相同 → 無差異，回 False（不算 updated）。"""
        from backend.app.importers.wips_importer import update_patent_changed_fields

        with self._conn() as c:
            c.execute(
                "INSERT INTO core_layer.patents (id, title, legal_status) VALUES (900004, '相同標題', '有效')")
            cur = c.cursor()
            changed = update_patent_changed_fields(
                cur, 900004, self._params(title="相同標題", legal_status="有效"))
            c.execute("ROLLBACK")
        self.assertFalse(changed, "所有欄位新值等於舊值時不得回 True")

    def test_noop_all_none_returns_false(self):
        """全部參數 None（無可更新）時 UPDATE 不命中，回 False。"""
        from backend.app.importers.wips_importer import update_patent_changed_fields

        with self._conn() as c:
            c.execute("INSERT INTO core_layer.patents (id, title) VALUES (900005, '既有標題')")
            cur = c.cursor()
            self.assertFalse(update_patent_changed_fields(cur, 900005, self._params()))
            c.execute("ROLLBACK")


if __name__ == "__main__":
    unittest.main()
