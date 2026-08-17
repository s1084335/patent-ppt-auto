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


class StackChartStillShowsStatusTests(unittest.TestCase):
    """⚠ 「現行有效」欄已於 2026-08-18 依使用者定案移除（「看圖就知道了」）。

    原本這裡三條測試在釘那一欄存在；反向契約已由
    `test_review_round_20260818.py` 的 `LiveCountRemovedTests` 承接，
    這裡只留「拿掉的是重複標示、不是資訊本身」這一條。
    """

    def test_granted_segment_and_total_remain(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "jurisdiction_distribution.svg"
            chart_runner.render_country_status_stack(
                path, "專利受理局分布",
                chart_runner.country_status_display_pivot(COUNTRY_ROWS))
            svg = path.read_text(encoding="utf-8")
        self.assertIn("授權", svg, "授權段是現在唯一的有效數來源，不得消失")
        self.assertIn("累計申請", svg)
        self.assertIn("24", svg)


class DesignStrategyMatrixTests(unittest.TestCase):
    """2026-08-18：矩陣改為申請人 × 技術／外觀（年度版與合計欄都已退場）。"""

    def test_axis_columns(self):
        rows = chart_runner.design_strategy_matrix_rows(STRATEGY_ROWS)
        self.assertEqual({r["strategy_axis"] for r in rows},
                         set(chart_runner.DESIGN_STRATEGY_AXIS))

    def test_counts_match_source(self):
        """⚠ 二輪拿掉「技術+外觀」合計欄，改驗兩欄各自等於來源件數。"""
        by = {(r["applicant"], r["strategy_axis"]): r["patent_count"]
              for r in chart_runner.design_strategy_matrix_rows(STRATEGY_ROWS)}
        for row in STRATEGY_ROWS:
            a = row["applicant"]
            self.assertEqual(by[(a, "技術")], row["tech_count"])
            self.assertEqual(by[(a, "外觀")], row["design_count"])


class IntersectionTableTrimTests(unittest.TestCase):
    def test_intersection_table_trimmed(self):
        trimmed = chart_runner.design_intersection_table_rows(INTERSECTION_ROWS)
        self.assertEqual(len(trimmed[0]), 5, trimmed[0].keys())
        for dropped in ("strategy_type", "representative_design_patent_id",
                        "representative_tech_patent_id", "tech_evidence", "has_figure"):
            self.assertNotIn(dropped, trimmed[0])

    def test_all_columns_have_chinese_labels(self):
        """表頭不得印英文 key——缺鍵是靜默失敗（實機看到 design_count）。"""
        # ⚠ 隱藏欄不需要中文欄名（它不上表頭）——`design_patent_ids` 是給
        #   解讀 CLI 取證用的，顯示層以 DATA_TABLE_EXCLUDED_COLUMNS 排除。
        hidden = set(chart_runner.DATA_TABLE_EXCLUDED_COLUMNS.get(
            "design_protection_detail", ()))
        for rows in (chart_runner.design_strategy_table_rows(STRATEGY_ROWS),
                     chart_runner.design_intersection_table_rows(INTERSECTION_ROWS)):
            for col in rows[0]:
                if col in hidden:
                    continue
                with self.subTest(col=col):
                    self.assertIn(col, chart_runner.DATA_COLUMN_LABELS)


if __name__ == "__main__":
    unittest.main()
