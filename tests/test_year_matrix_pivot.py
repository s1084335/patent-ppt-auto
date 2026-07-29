"""年度矩陣改交叉表 ＋ 專屬版面（2026-07-29 使用者定案）。

## 問題

使用者原話：「數據表是長格式，難讀」。

實測 `applicant_year_matrix` 45 列、`owner_year_matrix` 31 列，都是長格式
（公司、年份、件數各一列）——同一家公司的不同年份分散在不同列：

    XIAMEN DMASTER  2024  5
    XIAMEN DMASTER  2022  3     ← 同一家，要自己對照
    CHI HUA         2022  2

## 定案

**轉成交叉表**（公司一列、年份一欄、末欄合計）：

    公司            2021 2022 2023 2024  合計
    XIAMEN DMASTER    -    3    -    5     8
    CHI HUA           -    2    -    -     2

⚠ **轉置在後端做**——前端不必知道差異，符合「同一資訊一個落點」。

## 版面

使用者：「年度矩陣可以和其他種類報表的版面不同」。
交叉表欄多列少，與其他報表（左表右圖 45/55）相反，改**上下排列**：
交叉表滿寬在上、氣泡圖滿寬在下。
"""
from __future__ import annotations

import unittest


class PivotYearMatrixTests(unittest.TestCase):
    """長格式 → 交叉表。"""

    ROWS = [
        {"applicant_display_name": "A", "application_year": 2024, "patent_count": 5},
        {"applicant_display_name": "A", "application_year": 2022, "patent_count": 3},
        {"applicant_display_name": "B", "application_year": 2022, "patent_count": 2},
    ]

    def test_pivot_shape(self):
        from backend.app.reports.chart_runner import pivot_year_matrix

        rows = pivot_year_matrix(self.ROWS, "applicant_display_name")
        self.assertEqual(len(rows), 2, "兩家公司應各一列")
        a = next(r for r in rows if r["applicant_display_name"] == "A")
        self.assertEqual(a["2022"], 3)
        self.assertEqual(a["2024"], 5)
        self.assertEqual(a["total"], 8, "合計欄")

    def test_missing_year_is_blank_not_zero(self):
        """該公司該年沒有專利 → 空字串，不是 0。

        0 會讓表格看起來像「有查過但沒有」，空白才是「無此資料」。
        """
        from backend.app.reports.chart_runner import pivot_year_matrix

        rows = pivot_year_matrix(self.ROWS, "applicant_display_name")
        b = next(r for r in rows if r["applicant_display_name"] == "B")
        self.assertEqual(b["2024"], "", "無資料的年份應留空")

    def test_sorted_by_total_desc(self):
        """依合計降冪——排名意義才對。"""
        from backend.app.reports.chart_runner import pivot_year_matrix

        rows = pivot_year_matrix(self.ROWS, "applicant_display_name")
        self.assertEqual([r["applicant_display_name"] for r in rows], ["A", "B"])

    def test_year_columns_ascending(self):
        """年份欄由舊到新。"""
        from backend.app.reports.chart_runner import pivot_year_matrix

        rows = pivot_year_matrix(self.ROWS, "applicant_display_name")
        years = [k for k in rows[0] if k.isdigit()]
        self.assertEqual(years, sorted(years))

    def test_empty_input(self):
        from backend.app.reports.chart_runner import pivot_year_matrix

        self.assertEqual(pivot_year_matrix([], "applicant_display_name"), [])


class YearMatrixLayoutTests(unittest.TestCase):
    """版面：年度矩陣用上下排列，其餘報表左右 45/55。"""

    def test_definition_declares_layout(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        for name in ("applicant_year_matrix", "owner_year_matrix"):
            with self.subTest(report=name):
                self.assertEqual(getattr(REPORT_DEFINITIONS[name], "layout", None),
                                 "stacked", f"{name} 應宣告上下排列")

    def test_other_reports_default_layout(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        self.assertEqual(
            getattr(REPORT_DEFINITIONS["applicant_ranking"], "layout", "side_by_side"),
            "side_by_side", "一般報表維持左右分欄")


if __name__ == "__main__":
    unittest.main()
