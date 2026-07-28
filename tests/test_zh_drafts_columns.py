"""公司中文名草稿查詢的欄位必須真的存在（2026-07-27 實機 HTTP 500）。

實機症狀：瀏覽專利頁「公司中文名」區顯示「草稿載入失敗：HTTP 500」。
根因：`_LIST_DRAFTS_SQL` 查 `d.created_at`，但 `derived_layer.company_aliases`
**沒有這一欄**——實際欄位是 `imported_at`（匯入時間）與 `updated_at`（更新時間）。
PostgreSQL 甚至直接提示 `HINT: Perhaps you meant to reference the column "d.updated_at"`。

草稿是「一代碼至多一列、重跑會 UPDATE 收斂」（write_drafts 先刪後插），
故顯示「何時產的」應取 `updated_at`。

本測試不連 DB：以 SQL 內出現的欄位名對照 migration 建表語句，
在 CI 就能擋下「查了不存在的欄位」這類靜默到執行期才炸的錯。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORTER = PROJECT_ROOT / "backend" / "app" / "derived" / "company_alias_importer.py"

# derived_layer.company_aliases 的實際欄位（0030／0033／0040 migration 定版）。
# ⚠ 沒有 created_at——時間欄是 imported_at（匯入）與 updated_at（更新）。
# 0040（2026-07-28 四欄拆分）加了 公司中文名稱／正規化名稱 兩欄。
ACTUAL_COLUMNS = {
    "id", "申請人代碼", "公司名稱", "公司中文名稱", "正規化名稱", "別稱",
    "source_file", "imported_at", "alias_lookup_key", "source_type",
    "review_status", "wips_metadata_json", "updated_at",
}


class ZhDraftsColumnTests(unittest.TestCase):
    def _sql(self) -> str:
        src = IMPORTER.read_text(encoding="utf-8")
        m = re.search(r'_LIST_DRAFTS_SQL\s*=\s*"""(.*?)"""', src, re.S)
        self.assertIsNotNone(m, "找不到 _LIST_DRAFTS_SQL")
        return m.group(1)

    def test_no_created_at_as_source_column(self):
        """不得以 d.created_at 取值——該欄不存在，執行期直接 UndefinedColumn。

        ⚠ `AS created_at`（別名）可以保留：回應鍵名不變、呼叫端與前端不必改；
        受限的是**來源欄位**必須真的存在。
        """
        self.assertTrue(
            "d.created_at" not in self._sql(),
            "_LIST_DRAFTS_SQL 以 d.created_at 取值（實機 HTTP 500）；"
            "時間欄應為 d.updated_at")

    def test_referenced_columns_exist(self):
        """SQL 內以 d./c. 前綴引用的欄位，都必須是表的實際欄位。"""
        sql = self._sql()
        referenced = set(re.findall(r'\b[dc]\.(?:"([^"]+)"|([a-z_]+))', sql))
        names = {a or b for a, b in referenced}
        unknown = sorted(n for n in names if n not in ACTUAL_COLUMNS)
        self.assertEqual(
            unknown, [],
            f"SQL 引用了 company_aliases 沒有的欄位：{unknown}")


if __name__ == "__main__":
    unittest.main()
