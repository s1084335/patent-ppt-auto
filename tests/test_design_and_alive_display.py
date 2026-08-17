"""2026-08-17 使用者實物驗收三件：

1. 「現行有效跑去哪了？」——改六欄字面堆疊後，原本第二條 bar 的「現行有效」
   整個消失。它是決策口徑（權利活著幾件），不能只剩下讀者自己去加總。
2. 「策略分布你不如做矩陣給我」——申請人 × 年度矩陣取代兩條總數長條。
3. 「技術交叉的欄位能更精簡吧」——交叉表 11 欄砍到 5 欄，且表頭不得是英文 key。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_runner

# (受理局, 原始狀態, 件數)——含簡體與英文狀態，驗「現行有效」不是只等於「授權」欄。
COUNTRY_ROWS = [
    {"country_code": "CN", "legal_status": "授权", "patent_count": 24},
    {"country_code": "CN", "legal_status": "审查中", "patent_count": 5},
    {"country_code": "CN", "legal_status": "到期(Non-payment)", "patent_count": 7},
    {"country_code": "US", "legal_status": "granted", "patent_count": 3},
    {"country_code": "US", "legal_status": "审查中", "patent_count": 2},
]

STRATEGY_ROWS = [
    {"applicant": "廈門帝瑪斯健康科技", "strategy_type": "技術+外觀",
     "design_count": 2, "tech_count": 11,
     "first_design_year": 2019, "latest_design_year": 2022,
     "design_years": [2019, 2022], "legal_status_summary": "授權",
     "representative_design_patent_id": 134,
     "representative_design_title": "Fan skiing training ware"},
    {"applicant": "Zhou Zheng", "strategy_type": "只走外觀",
     "design_count": 1, "tech_count": 0,
     "first_design_year": 2021, "latest_design_year": 2021,
     "design_years": [2021], "legal_status_summary": "到期",
     "representative_design_patent_id": 129,
     "representative_design_title": "Body-building skiing machine"},
]

INTERSECTION_ROWS = [
    {"applicant": "廈門帝瑪斯健康科技", "strategy_type": "技術+外觀",
     "design_count": 2, "tech_count": 11,
     "tech_labels": ["阻力調節", "體感回饋"],
     "representative_design_patent_id": 134,
     "representative_design_title": "Fan skiing training ware",
     "representative_tech_patent_id": 88,
     "representative_tech_title": "滑雪機阻力控制裝置",
     "tech_evidence": "說明書第 3 段…", "has_figure": True},
]


class AliveCountRestoredTests(unittest.TestCase):
    """現行有效要在圖與表都看得到，且由狀態桶推導（不得在此另立一套判定）。"""

    def test_pivot_carries_alive_count(self):
        rows = chart_runner.country_status_display_pivot(COUNTRY_ROWS)
        cn = next(r for r in rows if r["country_code"] == "CN")
        self.assertEqual(cn["現行有效"], 24)

    def test_alive_not_merely_the_granted_column(self):
        """US 的 granted 是詞彙外的值、自成一欄——現行有效仍要算進去。

        ⚠ 這條是防「直接抓『授權』欄當現行有效」的偷懶寫法：那在中文資料上
        剛好相等，換一批英文登錄的資料就會少算。
        """
        rows = chart_runner.country_status_display_pivot(COUNTRY_ROWS)
        us = next(r for r in rows if r["country_code"] == "US")
        self.assertEqual(us["現行有效"], 3)
        self.assertNotIn("授權", [k for k, v in us.items() if v])

    def test_stack_chart_shows_alive_number(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "jurisdiction_distribution.svg"
            chart_runner.render_country_status_stack(
                path, "專利受理局分布",
                chart_runner.country_status_display_pivot(COUNTRY_ROWS))
            svg = path.read_text(encoding="utf-8")
        self.assertIn("現行有效", svg, "圖上看不到現行有效")
        self.assertIn("累計申請", svg)
        self.assertIn(">24<", svg.replace(" ", ""))


class DesignStrategyMatrixTests(unittest.TestCase):
    def test_matrix_rows_are_applicant_by_year(self):
        rows = chart_runner.design_year_matrix_rows(STRATEGY_ROWS)
        years = sorted({int(r["year"]) for r in rows})
        self.assertEqual(years, [2019, 2021, 2022])
        self.assertTrue(all(r["patent_count"] >= 1 for r in rows))

    def test_matrix_row_label_carries_strategy(self):
        """列標籤要帶策略型——否則矩陣只是年度分布，答不了「策略」。"""
        labels = {r["applicant_strategy"] for r
                  in chart_runner.design_year_matrix_rows(STRATEGY_ROWS)}
        self.assertTrue(any("技+外" in x for x in labels), labels)
        self.assertTrue(any("純外觀" in x for x in labels), labels)


class IntersectionTableTrimTests(unittest.TestCase):
    def test_intersection_table_trimmed(self):
        trimmed = chart_runner.design_intersection_table_rows(INTERSECTION_ROWS)
        self.assertEqual(len(trimmed[0]), 5, trimmed[0].keys())
        for dropped in ("strategy_type", "representative_design_patent_id",
                        "representative_tech_patent_id", "tech_evidence", "has_figure"):
            self.assertNotIn(dropped, trimmed[0])

    def test_all_columns_have_chinese_labels(self):
        """表頭不得印英文 key——缺鍵是靜默失敗（實機看到 design_count）。"""
        for rows in (chart_runner.design_strategy_table_rows(STRATEGY_ROWS),
                     chart_runner.design_intersection_table_rows(INTERSECTION_ROWS)):
            for col in rows[0]:
                with self.subTest(col=col):
                    self.assertIn(col, chart_runner.DATA_COLUMN_LABELS)


if __name__ == "__main__":
    unittest.main()
