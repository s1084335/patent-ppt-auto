"""申請人年度矩陣的數據表要吃交叉表（2026-08-12 使用者實機指出仍是長格式）。

## 症狀與根因（第三次同型接縫）

07-29 已做 `pivot_year_matrix`（「數據表是長格式，難讀」定案），但它落在
`ctx.chart_rows` 桶；顯示層（index 產表與 content API）2026-08-11 起**優先吃
`section["rows"]`**（受理局交叉表機制）——申請人卡沒帶 rows，前端仍回 reports
桶的長格式（申請人, 年份, 件數 逐列）。與 SECTION_PERSIST_KEYS 丟 rows、
pivot 進錯桶同屬「機制在、接縫沒接」。

## 契約

section 自帶 `rows`＝`pivot_year_matrix` 轉置（一列一申請人、年份成欄、
末欄 total 降冪、無資料年空字串）；`total` 欄有中文表頭。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_runner

ROWS = [
    {"applicant_display_name": "甲公司", "application_year": 2022, "patent_count": 3},
    {"applicant_display_name": "甲公司", "application_year": 2024, "patent_count": 5},
    {"applicant_display_name": "乙公司", "application_year": 2022, "patent_count": 2},
]


class ApplicantYearTablePivotTests(unittest.TestCase):
    def _section(self):
        class _Ctx:
            def __init__(self, tmp):
                self.run_dir = Path(tmp)
                self.sections = []
                self.chart_rows = {}

            def report(self, name):
                return {"label_zh": "申請人年度專利分布矩陣", "rows": ROWS}

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = _Ctx(tmp.name)
        chart_runner._build_applicant_year_matrix_section(ctx)
        return ctx.sections[0]

    def test_section_rows_are_summarised(self):
        """🔴 契約更新（2026-08-17 使用者「這樣做誰看得懂」→「這樣就好」）：

        section rows 從**年份展開交叉表**改為五欄摘要。原表把每個年份攤成
        一欄、大半是空格——稀疏矩陣不適合當表格，分布交給圖（已改跨度圖）。
        表格改回答：誰、幾件、活躍區間、跨幾年、最近一次。
        """
        rows = self._section().get("rows")
        self.assertTrue(rows, "section 未帶顯示用 rows——前端會退回長格式")
        top = rows[0]
        self.assertEqual(top["applicant_display_name"], "甲公司")
        self.assertEqual(top["patent_count"], 8)
        self.assertEqual(top["active_years"], "2022–2024")
        self.assertEqual(top["year_span"], 2)
        self.assertEqual(top["latest_year"], 2024)
        # ⚠ 年份不得再是欄位，否則就是舊的稀疏表
        self.assertNotIn("2022", top)

    def test_total_column_has_chinese_label(self):
        self.assertIn("total", chart_runner.DATA_COLUMN_LABELS)
        label = chart_runner.DATA_COLUMN_LABELS["total"]
        self.assertTrue(any("一" <= ch <= "鿿" for ch in label),
                        f"total 表頭仍是內部欄名：{label!r}")

    def test_rows_survive_persistence(self):
        """穿過 persistable_sections——白名單丟 rows 的舊坑不得重演。"""
        section = self._section()
        kept = chart_runner.persistable_sections([section])[0]
        self.assertIn("rows", kept)


if __name__ == "__main__":
    unittest.main()
