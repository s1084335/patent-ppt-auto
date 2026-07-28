"""受讓人納入同一套代碼收斂（2026-07-28 使用者定案：「受讓人也是用我這套收斂」）。

## 現況三層對照

              代碼對照   別稱對照   庫內代碼統計名
  申請人         ✅        ✅          ✅
  專利權人       ✅        ✅          ✅
  受讓人         ❌        ✅          ❌   ← 缺兩層

## 為什麼受讓人沒有代碼路徑

實查 WIPS 匯出（滑雪機.xlsx）的代碼欄只有三個：
    申请人名称标准化代码[JP]
    标准当前专利权人代码[US,JP,KR,CN,CA,AU]
    国家代码 / 指定国家代码
**沒有受讓人代碼欄**——所以無法像申請人／專利權人那樣以代碼歸戶。

## 使用者定案的解法

受讓人**共用使用者在代碼區塊建立的同一份對照表**：既然一組 = 代碼 + 正規化名稱 +
N 個變體，那該公司在受讓人欄出現的任何寫法，只要被列為變體，就該收斂到同一個
正規化名稱。差別只在「歸戶依據」是別稱字面而非代碼。

⚠ 關鍵補強：目前受讓人的別稱 LATERAL 只比對**受讓人欄自身**的字面。但使用者建的
變體清單是**跨欄位共用**的（同一家公司在申請人欄與受讓人欄可能寫法不同，兩者都會
被列進同一組變體）。故受讓人也應吃**整組變體**，而不是只吃剛好出現在受讓人欄的那個。

實測本庫受讓人只有 6/60 有值，影響面小；但機制要對，日後資料變多才不會又是一次
「同一概念兩套實作」。
"""
from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFRESH_SQL = (PROJECT_ROOT / "backend" / "app" / "derived"
               / "refresh_report_patent_base.py").read_text(encoding="utf-8")


class AssigneeUsesSharedAliasTests(unittest.TestCase):
    """受讓人要用同一份對照表收斂，不是自成一套。"""

    def test_assignee_display_has_code_layer(self):
        """受讓人的 COALESCE 要能吃到代碼對照結果。

        WIPS 無受讓人代碼欄，故不能直接 join 代碼；改為以「別稱→代碼」反查後
        取該代碼的公司名——等效於讓受讓人共用使用者建的同一組。
        """
        self.assertIn(
            "assignee_code_names", REFRESH_SQL,
            "受讓人沒有代碼層——使用者建的代碼組收斂不到受讓人欄")

    def test_assignee_still_has_alias_layer(self):
        """既有別稱路徑保留（不得因加代碼層而拿掉）。"""
        self.assertIn("assignee_alias", REFRESH_SQL)

    def test_assignee_coalesce_order(self):
        """順位與另兩欄一致：代碼對照 > 別稱對照 > 原始字面。"""
        idx_code = REFRESH_SQL.find("assignee_code_names")
        idx_alias = REFRESH_SQL.find('assignee_alias.display_name')
        self.assertGreater(idx_code, 0)
        self.assertGreater(idx_alias, 0)
        line_start = REFRESH_SQL.rfind("COALESCE(", 0, REFRESH_SQL.find(
            "AS recent_assignee_display_name"))
        line = REFRESH_SQL[line_start:REFRESH_SQL.find(
            "AS recent_assignee_display_name")]
        self.assertLess(
            line.find("acan_assignee"), line.find('assignee_alias.display_name'),
            "代碼對照必須排在別稱對照之前——代碼是使用者的裁決依據，優先級最高")

    def test_only_confirmed_aliases_used(self):
        """沿既有護欄：AI 草稿（ai_suggested）不得經任何路徑滲進正式顯示名。"""
        idx = REFRESH_SQL.find("assignee_code_names")
        block = REFRESH_SQL[idx:idx + 900]
        self.assertIn("confirmed", block)


class NoSecondImplementationTests(unittest.TestCase):
    """不得為受讓人另造一套收斂邏輯。"""

    def test_reuses_code_alias_names_cte(self):
        """代碼→公司名的對照 CTE 只能有一份（code_alias_names）。"""
        self.assertEqual(
            REFRESH_SQL.count("code_alias_names AS ("), 1,
            "出現第二份代碼對照 CTE——同一規則多處實作，必然分岔")


if __name__ == "__main__":
    unittest.main()
