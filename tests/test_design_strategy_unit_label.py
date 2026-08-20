"""策略型分布的單位是「家」不是「件」（2026-08-20 驗收發現）。

## 症狀

    chart_rows["design_protection_strategy"]
      = [{"strategy_type": "技術+設計", "patent_count": 4},
         {"strategy_type": "只走設計",  "patent_count": 1}]

`design_strategy_chart_rows` 是對**申請人列**做計數（4 家走技術+設計、1 家只走設計），
但欄名叫 `patent_count`，而 `table_display.column_labels` 把它顯示成「專利件數」。
實際那 5 家手上的設計案共 10 件——讀者看到「技術+設計 4 件」會直接算錯。

⚠ 數字本身沒錯，錯的是它自稱是什麼。這種缺陷驗不出來——加總、對帳全都過。
"""
from __future__ import annotations

import unittest

STRATEGY_ROWS = [
    {"applicant": "創科", "strategy_type": "技術+設計", "design_count": 4, "tech_count": 38},
    {"applicant": "寶時得", "strategy_type": "技術+設計", "design_count": 3, "tech_count": 9},
    {"applicant": "泉峰", "strategy_type": "技術+設計", "design_count": 1, "tech_count": 56},
    {"applicant": "牧田", "strategy_type": "技術+設計", "design_count": 1, "tech_count": 8},
    {"applicant": "王祥明", "strategy_type": "只走設計", "design_count": 1, "tech_count": 0},
]


class StrategyChartUnitTests(unittest.TestCase):

    def _rows(self):
        from backend.app.reports.chart_runner import design_strategy_chart_rows

        return design_strategy_chart_rows(STRATEGY_ROWS)

    def test_field_is_named_applicant_count(self):
        """🔴 計的是申請人列數，欄名就要叫 applicant_count。"""
        rows = self._rows()
        self.assertTrue(all("applicant_count" in r for r in rows),
                        f"欄名沒改，仍會被顯示成「專利件數」：{rows}")
        self.assertFalse(any("patent_count" in r for r in rows),
                         f"還留著誤導的欄名：{rows}")

    def test_values_are_applicant_counts(self):
        rows = {r["strategy_type"]: r["applicant_count"] for r in self._rows()}
        self.assertEqual(rows, {"技術+設計": 4, "只走設計": 1})
        # ⚠ 對照：這 5 家的設計案合計 10 件——與 4／1 完全是兩回事。
        self.assertEqual(sum(r["design_count"] for r in STRATEGY_ROWS), 10)

    def test_column_label_reads_as_applicants(self):
        """顯示標籤要跟著欄名走，不能兩邊各說各話。"""
        from backend.app.reports.chart_runner import table_display_spec

        spec = table_display_spec({})
        self.assertIn("家", spec["column_labels"]["applicant_count"])


if __name__ == "__main__":
    unittest.main()
