"""展開口徑下的 aggregate 語意（2026-08-06，Codex 驗收 A2／A3 揪出）。

## 為什麼會出問題

`applicant_ranking` 等三張報表在 2026-08-06 改讀**展開 VIEW**（共同申請人各自計數），
但那些 aggregate 是**為 base 表（一專利一列）設計的**，兩個假設在展開口徑下不成立：

| aggregate | base 下的假設 | 展開下為何錯 |
|---|---|---|
| `string_agg_co_values` | 多值欄第 1 個就是分組鍵本人，故取 `ord > 1` | 分組鍵可能是**第 2 個**（如「曾晴」），`ord > 1` 會把**自己**列成共同申請人、反而漏掉真正的夥伴 |
| `population_notes` 的輸入 | 傳 `persist_reports`（入庫截前 20 列） | 母體變成「前 20 名的加總」而非完整分析母體——**讀者看到錯的母體** |

⚠ 這兩個都不會讓測試變紅、也不會報錯，只會**默默給出錯的數字**。
"""
from __future__ import annotations

import unittest


class CoApplicantNamesTests(unittest.TestCase):
    """共同申請人名單：要排除「分組鍵本人」，不是排除「第 1 個」。"""

    def _sql(self):
        from backend.app.reports.report_engine import AGGREGATE_FUNCTIONS

        return AGGREGATE_FUNCTIONS["string_agg_co_values"]

    def test_excludes_the_group_key_not_the_first_element(self):
        """🔴 排除依據必須是**與分組鍵比對**，不能是序位。

        實例：曾晴那一列的原始申請人是 `廈門帝瑪斯 | 曾晴`，分組鍵是「曾晴」（第 2 個）。
        用 `ord > 1` 會留下曾晴自己、漏掉帝瑪斯——欄位語意整個反過來。
        """
        sql = self._sql()
        self.assertNotIn("_x.ord > 1", sql,
                         "仍以序位排除——分組鍵不一定是第 1 個（展開口徑下常是第 2 個）")
        self.assertIn("{group_col}", sql,
                      "排除條件沒有比對分組鍵，無法知道哪一個是「本人」")

    def test_comparison_uses_the_converged_name(self):
        """⚠ 比對要用**收斂後**的名字。

        分組鍵 `applicant_display_name` 是走過 `company_aliases` 的收斂名（中文），
        而多值欄拆出來的是原始字面（英文）。直接比字面會永遠不相等 → 本人不會被排除。
        """
        sql = self._sql()
        # 收斂後的運算式（公司中文名稱 → 正規化名稱 → 原字面）必須出現在比對的那一側
        self.assertIn('_ca."公司中文名稱"', sql)
        idx_compare = sql.find("{group_col}")
        self.assertGreater(idx_compare, 0)
        window = sql[max(0, idx_compare - 320):idx_compare]
        self.assertIn("_ca", window,
                      "比對分組鍵時沒有先收斂——中文分組鍵對英文原字面永遠不相等")


class PopulationUsesFullRowsTests(unittest.TestCase):
    """母體必須用完整 rows，不能用入庫截斷後的。"""

    def test_truncation_would_change_the_number(self):
        """先證明截斷確實會改變母體——否則這條測試沒有意義。"""
        from backend.app.reports.population import population_note

        full = [{"patent_count": 3} for _ in range(25)]        # 25 列，母體 75
        truncated = full[:20]                                   # 入庫截前 20，母體 60
        self.assertNotEqual(
            population_note("applicant_ranking", full, 55),
            population_note("applicant_ranking", truncated, 55),
            "截斷不影響母體的話，本測試無效")

    def test_chart_runner_passes_untruncated_reports(self):
        """🔴 `chart_runner` 必須把**未截斷**的 reports 餵給 `population_notes`。

        ⚠ `PERSIST_TOP20_REPORTS` 含 `applicant_ranking`／`ipc_main_distribution`／
        `cpc_main_distribution` 等；傳 `persist_reports` 進去，母體就變成前 20 列的加總。
        """
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "backend" / "app" / "reports"
               / "chart_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("population_notes({**persist_reports", src,
                         "母體吃到入庫截斷後的 rows——會顯示「前 20 列母體」")
        self.assertIn("population_notes({**fetched", src,
                      "母體應使用未截斷的 fetched")


if __name__ == "__main__":
    unittest.main()
