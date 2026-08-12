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

    def test_section_rows_are_pivoted(self):
        rows = self._section().get("rows")
        self.assertTrue(rows, "section 未帶顯示用交叉表 rows——前端會退回長格式")
        top = rows[0]
        self.assertEqual(top["applicant_display_name"], "甲公司")
        self.assertEqual(top["2022"], 3)
        self.assertEqual(top["2024"], 5)
        self.assertEqual(top["total"], 8)
        # 乙公司 2024 無資料＝空字串（0 讀起來像「查過但沒有」）
        self.assertEqual(rows[1]["2024"], "")

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
