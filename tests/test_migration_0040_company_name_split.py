"""0040 契約測試：company_aliases 公司名四欄拆分（2026-07-28 使用者定案）。

## 為什麼

現況 `derived_layer.company_aliases` 只有**一個** `公司名稱` 欄，混用兩種語意：
AI `translated` 寫中文、`keep_original` 寫英文原文、使用者手動建組也填這一欄。
使用者定案：「應該是 申請人代碼 / 公司中文名稱 / 正規化名稱 / 別稱，要這樣分」。

## 本測試鎖的契約

1. 新增 `公司中文名稱`、`正規化名稱` 兩欄，**皆可空**（使用者第②點：兩欄都可空、
   不加 CHECK；可能先建組之後才補名）。
2. **不搬資料**（使用者第③點：既有 TW-CHIHUA 那組由使用者從前端重走流程歸位；
   自動判斷「含 CJK 就是中文名」對混合字串會判錯且無人覆核）。
3. 舊 `公司名稱` 欄的 NOT NULL 必須放寬——四欄拆分後新寫入不再一定填它，
   留著 NOT NULL 會讓「只填中文名」的寫入直接 NotNullViolation。
4. 舊 UNIQUE (申請人代碼, 公司名稱, 別稱) 放寬：唯一性交給既有 partial unique
   index `ux_company_aliases_code_lookup_confirmed`。⚠ 放寬前確認過寫入路徑——
   全 repo 只有 `govern_company_names` 一處 `ON CONFLICT (三元組)` 依賴它，
   本輪同步改為依賴 partial index 的衝突目標。
5. downgrade 對稱：移欄、還原 NOT NULL 與舊 UNIQUE。

本測試不連 DB（migration 實跑另行以 alembic upgrade 驗證）：以原始碼契約擋下
「欄沒加」「downgrade 不對稱」這類到執行期才炸的錯。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "0040_company_name_split.py"

ZH_COL = "公司中文名稱"
EN_COL = "正規化名稱"
LEGACY_COL = "公司名稱"
LEGACY_UNIQUE = "company_aliases_申請人代碼_公司名稱_別稱_key"


def _strip_comments(text: str) -> str:
    """把 Python 註解與三引號字串以外的「可執行語句」留下。

    ⚠ 本專案今日已發生 5 次「測試假性通過」，其中一型正是**只斷言字串出現在整份
    檔案**——docstring 或註解提到欄名就被餵飽。故所有結構斷言先剝掉 `#` 註解與
    模組 docstring，只看真的會被執行的 SQL／op 呼叫。
    """
    # 去掉模組層 docstring（檔首那段三引號）
    text = re.sub(r'\A\s*(?:from __future__ import annotations\s*)?"""(?:.|\n)*?"""', "", text, count=1)
    # 去掉行內／整行 # 註解
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


class MigrationExistsTests(unittest.TestCase):
    def test_migration_file_exists(self):
        self.assertTrue(
            MIGRATION.exists(),
            "缺 0040 migration——四欄拆分的寫入路徑會 UndefinedColumn")


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MIGRATION.exists():
            raise unittest.SkipTest("migration 尚未建立")
        cls.src = MIGRATION.read_text(encoding="utf-8")
        cls.code = _strip_comments(cls.src)
        parts = cls.code.split("def downgrade")
        cls.up = parts[0]
        cls.down = parts[1] if len(parts) > 1 else ""

    def test_revision_chain(self):
        """head 目前是 0039_report_base_abstract。"""
        self.assertRegex(self.code, r'revision\s*=\s*["\']0040_company_name_split["\']')
        self.assertRegex(self.code, r'down_revision\s*=\s*["\']0039_report_base_abstract["\']')

    def test_upgrade_adds_both_columns(self):
        """兩欄都要加；只加一欄等於拆分沒完成。"""
        for col in (ZH_COL, EN_COL):
            self.assertIn(col, self.up, f"upgrade 未新增 {col} 欄")

    def test_new_columns_are_nullable(self):
        """使用者第②點：兩欄都可空、**不加 CHECK** 強制至少一欄有值。

        鎖真實行為：新欄的 add_column 不得帶 nullable=False，
        且整份 upgrade 不得對這兩欄建 CHECK。
        """
        self.assertNotRegex(
            self.up, r"nullable\s*=\s*False",
            "新欄不得 NOT NULL（使用者定：兩欄都可空，先建組後補名）")
        self.assertNotRegex(
            self.up, r"(?i)add\s+constraint[^\n]*check[^\n]*(公司中文名稱|正規化名稱)",
            "不得對新欄加 CHECK（使用者第②點明示不加）")

    def test_does_not_migrate_existing_rows(self):
        """使用者第③點：**不搬資料**，只加欄。

        自動判斷「含 CJK 就是中文名」對混合字串（XIAMEN ... | Zeng Qing）會判錯，
        且沒有人覆核。既有 TW-CHIHUA 那組由使用者從前端重走流程歸位。
        """
        self.assertNotRegex(
            self.up, r"(?i)\bUPDATE\s+derived_layer\.company_aliases",
            "upgrade 出現 UPDATE——違反「不寫自動遷移」定案")
        self.assertNotIn(
            "一-鿿", self.up,
            "upgrade 出現 CJK 判斷——那正是使用者否決的自動猜測遷移")


class EmittedSqlTests(unittest.TestCase):
    """真的跑 upgrade()／downgrade()，側錄實際送出的 SQL 與 op 呼叫。

    ⚠ 只讀原始碼會被 f-string／常數變數騙過（本測試初版就因 LEGACY_UNIQUE
    寫成常數而抓不到約束名）。這裡以假 `op` 執行，驗**真的會執行到什麼**。
    """

    @classmethod
    def setUpClass(cls):
        if not MIGRATION.exists():
            raise unittest.SkipTest("migration 尚未建立")
        cls.module = cls._load()

    @staticmethod
    def _load():
        import importlib.util

        spec = importlib.util.spec_from_file_location("mig0040", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run(self, func_name):
        """以假 op 執行 upgrade／downgrade，回 (執行的 SQL 串, op 呼叫紀錄)。"""
        from unittest import mock

        sqls: list[str] = []
        calls: list[tuple] = []
        fake_op = mock.MagicMock()
        fake_op.execute.side_effect = lambda sql, *a, **k: sqls.append(str(sql))
        fake_op.add_column.side_effect = lambda *a, **k: calls.append(("add_column", a, k))
        fake_op.drop_column.side_effect = lambda *a, **k: calls.append(("drop_column", a, k))
        fake_op.alter_column.side_effect = lambda *a, **k: calls.append(("alter_column", a, k))
        with mock.patch.object(self.module, "op", fake_op):
            getattr(self.module, func_name)()
        return "\n".join(sqls), calls

    def test_upgrade_adds_two_nullable_columns(self):
        _, calls = self._run("upgrade")
        added = [c for c in calls if c[0] == "add_column"]
        self.assertEqual(len(added), 2, "upgrade 未新增剛好兩欄")
        names = []
        for _, args, _kw in added:
            column = args[1]
            names.append(column.name)
            self.assertTrue(column.nullable, f"{column.name} 不可為 NOT NULL（使用者第②點）")
        self.assertEqual(sorted(names), sorted([ZH_COL, EN_COL]))

    def test_upgrade_drops_legacy_not_null_and_unique(self):
        sql, _ = self._run("upgrade")
        self.assertRegex(
            sql, r'(?i)ALTER COLUMN "公司名稱" DROP NOT NULL',
            "未放寬舊 公司名稱 的 NOT NULL——「只填中文名」的寫入會 NotNullViolation")
        self.assertIn(LEGACY_UNIQUE, sql, "未 DROP 舊三元組 UNIQUE 約束")
        self.assertRegex(sql, r"(?i)DROP CONSTRAINT")

    def test_upgrade_emits_no_data_migration(self):
        """使用者第③點：只加欄、不搬資料。"""
        sql, _ = self._run("upgrade")
        self.assertNotRegex(sql, r"(?i)\bUPDATE\b", "upgrade 送出 UPDATE——違反不自動遷移定案")

    def test_downgrade_is_symmetric(self):
        """downgrade 三件事都要還原：移兩欄、還原 NOT NULL、還原舊 UNIQUE。"""
        sql, calls = self._run("downgrade")
        dropped = sorted(c[1][1] for c in calls if c[0] == "drop_column")
        self.assertEqual(dropped, sorted([ZH_COL, EN_COL]), "downgrade 未移除兩個新欄")
        self.assertRegex(sql, r'(?i)ALTER COLUMN "公司名稱" SET NOT NULL',
                         "downgrade 未還原舊欄 NOT NULL")
        self.assertIn(LEGACY_UNIQUE, sql, "downgrade 未還原舊三元組 UNIQUE")
        self.assertRegex(sql, r"(?i)ADD CONSTRAINT")

    def test_downgrade_backfills_before_not_null(self):
        """還原 NOT NULL 前必須先補值，否則拆欄後寫的列（舊欄空）會直接失敗。"""
        sql, _ = self._run("downgrade")
        self.assertRegex(sql, r"(?i)UPDATE derived_layer\.company_aliases")
        self.assertLess(
            sql.upper().index("UPDATE"), sql.upper().index("SET NOT NULL"),
            "補值必須排在 SET NOT NULL 之前")


class SchemaCommentsTests(unittest.TestCase):
    """欄位註解同步（schema_comments 是 DB 註解的唯一來源）。"""

    def test_new_columns_documented(self):
        from backend.app.db import schema_comments

        cols = schema_comments.COMMENTS["derived_layer.company_aliases"]
        self.assertIn(ZH_COL, cols, "schema_comments 缺 公司中文名稱 註解")
        self.assertIn(EN_COL, cols, "schema_comments 缺 正規化名稱 註解")


if __name__ == "__main__":
    unittest.main()
