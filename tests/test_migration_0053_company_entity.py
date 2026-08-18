"""0053 契約測試：公司實體表與集團成員的參照完整性。

## 本測試鎖的契約

1. 版本鏈接在 0052 之後（0052 的別稱唯一性是本輪回填能成立的前提）。
2. 建 `derived_layer.companies`，`company_code` 為 PK、`is_temp` 為衍生欄
   （GENERATED ALWAYS ... STORED——不是可寫欄位，寫不進去就不會漂掉）。
3. 回填取「別稱表 ∪ 集團成員表」：只取其中一邊，另一邊的代碼會在加外鍵時炸。
4. 外鍵子句必須是 `ON UPDATE CASCADE` ＋ `ON DELETE RESTRICT`——
   ⚠ 這兩個子句是本 change 的**全部價值**：
   - CASCADE 讓「promote 漏更新集團成員」寫不出來
   - RESTRICT 讓「刪代碼留下集團孤兒」寫不出來，且把靜默副作用變成明確動作
   任一個被改成別的語意，約束就從「擋錯」變成「幫忙做錯」，而且**不會有任何測試變紅**
   ——除非鎖在這裡。
5. downgrade 對稱：先卸外鍵再刪表（反序會被外鍵擋住）。

本測試不連 DB（實庫行為另以 `scripts/` 的反向驗證確認：真的擋、真的連動）；
這裡以假 `op` 側錄實際送出的 SQL，擋下「只讀原始碼會被常數／f-string 騙過」那一型。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "0053_company_entity.py"


def _strip_sql_comments(sql: str) -> str:
    """剝掉 SQL 的 `--` 註解與 `/* */` 區塊註解。

    ⚠ 2026-08-18 變異檢查抓到：把 `UNION` 改成 `-- UNION`（整段回填被註解掉）時，
    只斷言字串出現的測試照樣通過——測試被註解裡的字餵飽了。同一型在 0040 已警告過。
    所有斷言一律先過這裡，只看**真的會被執行**的 SQL。
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


class MigrationExistsTests(unittest.TestCase):
    def test_migration_file_exists(self):
        self.assertTrue(MIGRATION.exists(), "缺 0053 migration——外鍵不存在，孤兒照樣產生")


class EmittedSqlTests(unittest.TestCase):
    """真的跑 upgrade()／downgrade()，側錄實際送出的 SQL。"""

    @classmethod
    def setUpClass(cls):
        if not MIGRATION.exists():
            raise unittest.SkipTest("migration 尚未建立")
        import importlib.util

        spec = importlib.util.spec_from_file_location("mig0053", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.module = module

    def _run(self, func_name: str) -> str:
        sqls: list[str] = []
        fake_op = mock.MagicMock()
        fake_op.execute.side_effect = lambda sql, *a, **k: sqls.append(str(sql))
        with mock.patch.object(self.module, "op", fake_op):
            getattr(self.module, func_name)()
        return _strip_sql_comments("\n".join(sqls))

    def test_revision_chain(self):
        src = MIGRATION.read_text(encoding="utf-8")
        self.assertRegex(src, r'revision\s*=\s*["\']0053_company_entity["\']')
        self.assertRegex(src, r'down_revision\s*=\s*["\']0052_alias_lookup_single_code["\']')

    def test_creates_companies_with_pk(self):
        sql = self._run("upgrade")
        self.assertRegex(sql, r"(?is)CREATE TABLE.*derived_layer\.companies")
        self.assertRegex(sql, r"(?i)company_code\s+TEXT\s+PRIMARY KEY",
                         "company_code 不是 PK——外鍵指不過去")

    def test_is_temp_is_generated_not_writable(self):
        """衍生欄：寫不進去，就不會與 company_code 漂開。"""
        sql = self._run("upgrade")
        self.assertRegex(
            sql, r"(?is)is_temp\s+BOOLEAN\s+GENERATED ALWAYS AS.*STORED",
            "is_temp 不是衍生欄——變成可寫欄位後遲早與代碼不一致")

    def test_backfills_from_both_tables(self):
        """只回填一邊，另一邊的代碼會讓 ADD CONSTRAINT 直接失敗。"""
        sql = self._run("upgrade")
        self.assertRegex(sql, r"(?is)INSERT INTO derived_layer\.companies")
        self.assertIn("company_aliases", sql, "回填未取別稱表")
        self.assertIn("company_group_members", sql, "回填未取集團成員表")
        self.assertRegex(sql, r"(?i)\bUNION\b", "兩邊未取聯集")

    def test_foreign_key_clauses(self):
        """⚠ 本 change 的全部價值在這兩個子句上。"""
        sql = self._run("upgrade")
        self.assertRegex(
            sql, r"(?is)ALTER TABLE derived_layer\.company_group_members\s+ADD CONSTRAINT",
            "未對集團成員加外鍵")
        self.assertRegex(
            sql, r"(?is)REFERENCES\s+derived_layer\.companies\s*\(\s*company_code\s*\)",
            "外鍵未指向 companies(company_code)")
        self.assertRegex(sql, r"(?i)ON UPDATE\s+CASCADE",
                         "缺 ON UPDATE CASCADE——promote 又會漏更新集團成員")
        self.assertRegex(sql, r"(?i)ON DELETE\s+RESTRICT",
                         "缺 ON DELETE RESTRICT——刪代碼會靜默清掉集團成員或留下孤兒")
        self.assertNotRegex(
            sql, r"(?i)ON DELETE\s+(CASCADE|SET NULL)",
            "ON DELETE 不得為 CASCADE／SET NULL——那是「幫忙做錯」，不是擋錯")

    def test_downgrade_drops_fk_before_table(self):
        """反序會被外鍵擋住，downgrade 直接失敗。"""
        sql = self._run("downgrade")
        self.assertRegex(sql, r"(?i)DROP CONSTRAINT", "downgrade 未卸外鍵")
        # ⚠ 卸錯名字時 IF EXISTS 不報錯，要到 DROP TABLE 才被真外鍵擋住——
        #   錯誤會出現在「後面那一句」，看起來像刪表壞了。故指名鎖住約束名。
        self.assertIn(
            "fk_company_group_members_company", sql,
            "downgrade 卸的不是本輪加的那條外鍵——IF EXISTS 會靜默略過，DROP TABLE 才炸")
        self.assertRegex(sql, r"(?is)DROP TABLE.*derived_layer\.companies",
                         "downgrade 未刪表")
        self.assertLess(
            sql.upper().index("DROP CONSTRAINT"), sql.upper().index("DROP TABLE"),
            "順序錯了：先刪表會被外鍵擋住")


if __name__ == "__main__":
    unittest.main()
