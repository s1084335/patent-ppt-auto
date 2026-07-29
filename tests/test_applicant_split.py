"""共同申請人在分析統計中拆開計數（R4，獨立一批做）。

## 使用者定案（三層各自不同，不可一概而論）

| 層 | 處理 | 狀態 |
|---|---|---|
| **詳情層顯示** | **保留完整字面** `A \| B` | ✅ 已符合，不動 |
| **待補專利權人代碼** | 拆開 | ✅ 2026-07-28 `acdf359` 已修 |
| **分析統計**（排名／交叉表／年度矩陣） | **拆成兩筆各自計數** | 🔴 本檔要做 |

⚠ 件數總和會大於專利總數（共同申請一筆算兩家）——使用者確認「這是專利分析慣例」。
報表需加註說明。

⚠ 實作用 **VIEW** 而非改 `report_patent_base`：後者「一專利一列」的語意必須保持
（詳情層與其他 14 個報表都依賴它）。展開只給三個申請人報表用。

⚠ **為何獨立一批**：R1-R3 是純呈現、改壞一看就知道；本項動的是統計數字，
改壞了會產出**看似合理但錯誤**的數據——驗收要逐一核對「Zeng Qing 是否進排名、
件數是否正確、其他 14 個報表有沒有被波及」，與驗版面是兩種心智模式。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class R4ApplicantSplitTests(unittest.TestCase):
    """R4：分析統計拆共同申請人；詳情層保留完整字面。"""

    def test_expanded_view_migration_exists(self):
        """需有 migration 建展開 VIEW。"""
        versions = PROJECT_ROOT / "alembic" / "versions"
        hits = [p for p in versions.glob("*.py")
                if "applicant" in p.name and "expand" in p.name]
        self.assertTrue(hits, "缺展開 VIEW 的 migration")

    def test_three_reports_use_expanded_source(self):
        """三個申請人報表改指展開來源；其餘報表不得受影響。"""
        from backend.app.reports.report_definitions import (
            REPORT_DEFINITIONS,
            REPORT_SOURCE_TABLE,
        )

        expanded = {"applicant_ranking", "applicant_country_distribution",
                    "applicant_year_matrix"}
        for name in expanded:
            with self.subTest(report=name):
                self.assertNotEqual(
                    REPORT_DEFINITIONS[name].source_table, REPORT_SOURCE_TABLE,
                    f"{name} 仍讀一專利一列的寬表，共同申請人算不進去")

        # 其餘**原本就讀 report_patent_base** 的報表必須維持原來源
        # ——一專利一列的語意不能被破壞。
        # ⚠ 排除本來就用別張表的（family_* 走家族表、cluster 型不走 SQL），
        # 否則會把「本來就不同」誤判成「被改壞」（本測試初版即如此）。
        untouched = {
            name for name, d in REPORT_DEFINITIONS.items()
            if name not in expanded
            and d.report_type == "aggregate"
            and not d.source_table.endswith(("report_family_country", "report_family_quality"))
        }
        for name in untouched:
            with self.subTest(report=name):
                self.assertEqual(REPORT_DEFINITIONS[name].source_table, REPORT_SOURCE_TABLE,
                                 f"{name} 不該改來源（會重複計數）")

    def test_detail_display_keeps_full_text(self):
        """⚠ 詳情層的「申請人」必須保留完整 `A | B`（使用者明示要維持）。"""
        sql = (PROJECT_ROOT / "backend" / "app" / "derived"
               / "refresh_report_patent_base.py").read_text(encoding="utf-8")
        # ⚠ 逐行比對，不用 assertRegex 的 ^$（預設非 multiline，會整份當一行而永遠不匹配
        # ——本測試初版即如此假失敗）。
        lines = [l.strip() for l in sql.splitlines()]
        self.assertIn('b."申請人",', lines,
                      "原始申請人欄被改動了——詳情層要顯示完整字面 `A | B`")


if __name__ == "__main__":
    unittest.main()
