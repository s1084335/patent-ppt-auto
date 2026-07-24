"""公司中文名 AI 草稿——DB 落點三態測試（拋棄式 DB）。

驗證沿用 company_aliases 的三態機制：
- 未判斷：canonical 無 CJK、無 curation 裁決列、無 AI 草稿列 → 進待中文化清單。
- AI 草稿待確認：review_status='ai_suggested' 列（含 verdict）→ 不進 code_alias_names
  （只採 confirmed）、也不再重複進待中文化清單（跳過已有草稿）。
- 已確認：review_status='confirmed'（既有 apply_confirmed_display_names，含保留原文）→ 不再浮現。

另驗 migration constraint 已放行 ai_suggested，且同代碼多列草稿以一次 UPDATE 收斂。
需 RUN_DB_TESTS=1。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

TEST_DB = "patent_ppt_zh_name"
HEAD_REV = "head"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _kw(dbname: str) -> dict:
    kw = dict(host=os.getenv("PGHOST", "127.0.0.1"), port=int(os.getenv("PGPORT", "5433")),
              user=os.getenv("PGUSER", "postgres"), dbname=dbname)
    if os.getenv("PGPASSWORD"):
        kw["password"] = os.environ["PGPASSWORD"]
    return kw


def _rw(dbname: str) -> dict:
    kw = _kw(dbname)
    kw["options"] = "-c search_path=derived_layer,core_layer,raw_layer,public"
    return kw


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "需 RUN_DB_TESTS=1 與可用 PostgreSQL")
class CompanyZhNameStoreTests(unittest.TestCase):
    """在拋棄式 DB 上驗證 CompanyZhNameStore 的三態取數與草稿寫入。"""

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

    def setUp(self):
        """每個測試前清空對照表，避免互相污染。"""
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute("TRUNCATE derived_layer.company_aliases RESTART IDENTITY CASCADE")
            c.commit()

    def _seed(self, code, name, alias=None, source_file="setup",
              review_status="confirmed", source_type="excel_seed"):
        with psycopg.connect(**_rw(TEST_DB)) as c:
            c.execute(
                'INSERT INTO derived_layer.company_aliases '
                '("申請人代碼", "公司名稱", "別稱", source_file, review_status, source_type) '
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (code, name, alias or name, source_file, review_status, source_type))
            c.commit()

    def _store(self):
        from backend.app.worker.ai_company_zh_name_runner import CompanyZhNameStore
        return CompanyZhNameStore(connect_kwargs=_rw(TEST_DB))

    # ── migration constraint ──────────────────────────────────

    def test_ai_suggested_status_allowed_by_constraint(self):
        """0033 起 constraint 須放行 review_status/source_type = 'ai_suggested'。"""
        # 若 constraint 未放行，這個 INSERT 會拋 CheckViolation。
        self._seed("A001", "Chervon", source_file="ai:company_zh_name",
                   review_status="ai_suggested", source_type="ai_suggested")
        with psycopg.connect(**_rw(TEST_DB)) as c:
            n = c.execute(
                "SELECT count(*) FROM derived_layer.company_aliases "
                "WHERE review_status='ai_suggested'").fetchone()[0]
        self.assertEqual(n, 1)

    # ── 三態取數 ──────────────────────────────────────────────

    def test_pending_lists_undetermined_english_company(self):
        """未判斷（英文 canonical、無裁決、無草稿）→ 進待中文化清單。"""
        self._seed("E001", "Milwaukee Tool")
        pending = self._store().fetch_pending()
        codes = {code for code, _ in pending}
        self.assertIn("E001", codes)

    def test_pending_excludes_chinese_canonical(self):
        """中文 canonical → 不進清單。"""
        self._seed("Z001", "泉峰")
        pending = self._store().fetch_pending()
        self.assertNotIn("Z001", {code for code, _ in pending})

    def test_pending_excludes_confirmed_curation(self):
        """已確認（含保留原文，source_file 為 curation 裁決標記）→ 不再浮現。"""
        self._seed("K001", "Keep Original Co",
                   source_file="display_name_curation_20260724")
        pending = self._store().fetch_pending()
        self.assertNotIn("K001", {code for code, _ in pending})

    def test_pending_excludes_codes_with_existing_ai_draft(self):
        """已有 AI 草稿列（ai_suggested）→ 不重複進清單（避免每次重問同批、燒 token）。"""
        self._seed("D001", "Some Foreign Co")  # confirmed 英文正名（未判斷來源）
        self._seed("D001", "某外國公司", source_file="ai:company_zh_name",
                   review_status="ai_suggested", source_type="ai_suggested")
        pending = self._store().fetch_pending()
        self.assertNotIn("D001", {code for code, _ in pending})

    # ── 草稿寫入不進正式顯示欄 ────────────────────────────────

    def test_draft_write_does_not_enter_confirmed_display_name(self):
        """草稿寫入後，refresh 用的 confirmed 收斂（只採 confirmed）讀不到草稿中文名。"""
        self._seed("F001", "Chervon")
        store = self._store()
        store.write_drafts([
            {"company_code": "F001", "zh_name": "泉峰",
             "verdict": "translated", "review_status": "ai_suggested"}
        ])
        # 模擬 refresh 的 code_alias_names：只採 confirmed 列取公司名稱。
        with psycopg.connect(**_rw(TEST_DB)) as c:
            confirmed_name = c.execute(
                'SELECT mode() WITHIN GROUP (ORDER BY "公司名稱") '
                "FROM derived_layer.company_aliases "
                "WHERE \"申請人代碼\"='F001' AND review_status='confirmed'").fetchone()[0]
        # confirmed 收斂結果仍是英文原名，草稿中文名沒混進去。
        self.assertEqual(confirmed_name, "Chervon")
        # 但草稿列確實存在（待使用者確認）。
        with psycopg.connect(**_rw(TEST_DB)) as c:
            draft = c.execute(
                'SELECT "公司名稱" FROM derived_layer.company_aliases '
                "WHERE \"申請人代碼\"='F001' AND review_status='ai_suggested'").fetchone()
        self.assertEqual(draft[0], "泉峰")

    def test_rewrite_draft_updates_same_code_in_one_pass(self):
        """重跑草稿：同代碼既有草稿以一次 UPDATE 收斂為新值，不堆疊多列草稿。"""
        self._seed("G001", "Chervon")
        store = self._store()
        store.write_drafts([{"company_code": "G001", "zh_name": "泉峰舊",
                             "verdict": "translated", "review_status": "ai_suggested"}])
        store.write_drafts([{"company_code": "G001", "zh_name": "泉峰",
                             "verdict": "translated", "review_status": "ai_suggested"}])
        with psycopg.connect(**_rw(TEST_DB)) as c:
            rows = c.execute(
                'SELECT "公司名稱" FROM derived_layer.company_aliases '
                "WHERE \"申請人代碼\"='G001' AND review_status='ai_suggested'").fetchall()
        self.assertEqual(len(rows), 1, "同代碼草稿應唯一，不堆疊")
        self.assertEqual(rows[0][0], "泉峰")


if __name__ == "__main__":
    unittest.main()
