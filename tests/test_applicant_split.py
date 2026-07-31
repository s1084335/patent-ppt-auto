"""申請人／專利權人統計口徑：**分析只計第一順位**（2026-07-31 使用者定案）。

## 沿革（⚠ 本檔前身是反向契約）

2026-07-28（R4）曾定「共同申請人拆開各自計數」並建展開 VIEW（0042）。
2026-07-31 使用者**推翻**：「專利權人和申請人都只計算第一順位，
瀏覽專利那裏一樣都要顯示，只有分析時沒有去計算第二順位」。

| 層 | 處理 |
|---|---|
| 瀏覽專利／詳情顯示 | **保留完整字面 `A | B`**（不動） |
| 分析統計（排名／交叉表／年度矩陣／家數） | **只計第一順位** |

- 權人與分群家數本來就走 `report_patent_base` 的 split_part 第一順位欄，無需改。
- 申請人三報表由展開 VIEW 改回 `report_patent_base.applicant_display_name`。
- 0042 的 VIEW 與 migration **保留不刪**（架構不動），僅停止引用。
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FirstPositionOnlyTests(unittest.TestCase):
    """分析統計一律第一順位；顯示層完整字面不動。"""

    def test_all_aggregate_reports_use_base_table(self):
        """全部 aggregate 報表（含申請人三張）都讀一專利一列的寬表＝只計第一順位。"""
        from backend.app.reports.report_definitions import (
            REPORT_DEFINITIONS,
            REPORT_SOURCE_TABLE,
        )

        for name, d in REPORT_DEFINITIONS.items():
            if d.report_type != "aggregate":
                continue
            if d.source_table.endswith(("report_family_country", "report_family_quality")):
                continue
            with self.subTest(report=name):
                self.assertEqual(
                    d.source_table, REPORT_SOURCE_TABLE,
                    f"{name} 不是第一順位口徑（仍讀展開表會把共同申請人各自計數）")

    def test_expanded_view_not_referenced(self):
        """⚠ 展開 VIEW 保留但不得再被任何報表引用。"""
        from backend.app.reports.report_definitions import (
            APPLICANT_EXPANDED_TABLE,
            REPORT_DEFINITIONS,
        )

        users = [n for n, d in REPORT_DEFINITIONS.items()
                 if d.source_table == APPLICANT_EXPANDED_TABLE]
        self.assertEqual(users, [], f"仍有報表引用展開表：{users}")

    def test_base_columns_are_first_position(self):
        """寬表的顯示名欄必須是 split_part(...,1)——第一順位的實作位置。"""
        sql = (PROJECT_ROOT / "backend" / "app" / "derived"
               / "refresh_report_patent_base.py").read_text(encoding="utf-8")
        self.assertIn("split_part", sql)
        self.assertIn('第一個', sql)

    def test_detail_display_keeps_full_text(self):
        """⚠ 瀏覽／詳情層的「申請人」保留完整 `A | B`（使用者明示不動）。"""
        sql = (PROJECT_ROOT / "backend" / "app" / "derived"
               / "refresh_report_patent_base.py").read_text(encoding="utf-8")
        lines = [l.strip() for l in sql.splitlines()]
        self.assertIn('b."申請人",', lines,
                      "原始申請人欄被改動了——顯示層要完整字面 `A | B`")


if __name__ == "__main__":
    unittest.main()
