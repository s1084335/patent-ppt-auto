"""2026-08-18 實物驗收五項。

1. 現行有效不特別點出來——看圖（授權那段）就知道了。
2. 設計保護策略改「申請人 × 技術／設計／技術+設計」矩陣。
3. 技術交叉表要呈現**設計所保護的標的**（讀設計案內容進來）。
   ⚠ 現行的「技術主題」欄永遠是空的：`_tech_label` 找 分類標籤／topic_label／
   topic_key／label，而 `design_protection_detail` 報表根本沒有這些欄
   ——空欄比沒有欄更糟，讀者會以為「這些申請人沒有技術主題」。
4. 主題演進的表格要與它的圖對得上。⚠ 現行掛的是**主題統計表**那份 rows，
   所以兩個分頁的表一模一樣（使用者：「視同一張」）。
5. `granted_pending`／`種類組成` 要標清楚單位（前者連中文欄名都沒有，
   實機表頭直接印英文 key）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner
from backend.app.reports.content_blocks import (
    design_protection_strategy,
    design_tech_intersections,
)

# 逐件設計／技術案（仿 design_protection_detail 的 rows）
DETAIL = [
    {"patent_id": 134, "patent_type": "設計", "document_kind": "S",
     "application_year": 2019, "country_code": "CN", "legal_status": "授权",
     "applicant_display_name": "廈門帝瑪斯健康科技", "title": "Fan skiing training ware",
     "主權項": ""},
    {"patent_id": 135, "patent_type": "設計", "document_kind": "S",
     "application_year": 2022, "country_code": "CN", "legal_status": "授权",
     "applicant_display_name": "廈門帝瑪斯健康科技", "title": "Ski simulator handle",
     "主權項": ""},
    {"patent_id": 88, "patent_type": "發明", "document_kind": "A",
     "application_year": 2020, "country_code": "CN", "legal_status": "授权",
     "applicant_display_name": "廈門帝瑪斯健康科技", "title": "滑雪機阻力控制裝置",
     "主權項": "一種阻力控制裝置…"},
    {"patent_id": 129, "patent_type": "設計", "document_kind": "S",
     "application_year": 2021, "country_code": "CN", "legal_status": "到期",
     "applicant_display_name": "Zhou Zheng", "title": "Body-building skiing machine",
     "主權項": ""},
]

COUNTRY_ROWS = [
    {"country_code": "CN", "legal_status": "授权", "patent_count": 24},
    {"country_code": "CN", "legal_status": "审查中", "patent_count": 5},
]


class LiveCountRemovedTests(unittest.TestCase):
    """1. 現行有效不再單獨標示——堆疊的「授權」段已經在講同一件事。"""

    def _svg(self) -> str:
        rows = chart_runner.country_status_display_pivot(COUNTRY_ROWS)
        out = Path(tempfile.mkdtemp()) / "x.svg"
        chart_runner.render_country_status_stack(out, "專利受理局分布", rows)
        return out.read_text(encoding="utf-8")

    def test_chart_has_no_live_column(self):
        svg = self._svg()
        self.assertNotIn("現行有效", svg, "圖上仍有現行有效欄")
        self.assertIn("累計申請", svg, "累計申請要留著")

    def test_table_has_no_live_column(self):
        for row in chart_runner.country_status_display_pivot(COUNTRY_ROWS):
            self.assertNotIn("現行有效", row)
            self.assertNotIn("現存有效", row)

    def test_granted_segment_still_there(self):
        """⚠ 拿掉的是「另外標一次」，不是拿掉資訊本身。"""
        self.assertIn("授權", self._svg())


class StrategyMatrixTests(unittest.TestCase):
    """2. 申請人 × 技術／設計／技術+設計。"""

    def _rows(self):
        return chart_runner.design_strategy_matrix_rows(
            design_protection_strategy(DETAIL))

    def test_two_columns(self):
        """⚠ 2026-08-18 二輪定案：拿掉「技術+設計」。這張圖只收有設計案的
        申請人，第三欄恆等於前兩欄相加，永遠不會出現只走技術那一類。"""
        cols = {r["strategy_axis"] for r in self._rows()}
        self.assertEqual(cols, {"技術", "設計"})

    def test_counts_per_applicant(self):
        by = {(r["applicant"], r["strategy_axis"]): r["patent_count"]
              for r in self._rows()}
        self.assertEqual(by[("廈門帝瑪斯健康科技", "設計")], 2)
        self.assertEqual(by[("廈門帝瑪斯健康科技", "技術")], 1)

    def test_design_only_applicant_has_no_tech_cell(self):
        """只走設計者技術欄應為 0——不得憑空生出技術件數。"""
        by = {(r["applicant"], r["strategy_axis"]): r["patent_count"]
              for r in self._rows()}
        self.assertEqual(by.get(("Zhou Zheng", "技術"), 0), 0)
        self.assertEqual(by[("Zhou Zheng", "設計")], 1)


class IntersectionTableSupersededTests(unittest.TestCase):
    """⚠ 本輪的「設計保護標的」欄已於同日二輪移交解讀（使用者：「應該納入解讀
    讓 CLI 去讀來寫」）——表格改為逐家時序表，判準見
    `test_design_cross_timeline.py`。這裡只留兩條防回頭的斷言。
    """

    def _rows(self):
        return chart_runner.design_intersection_table_rows(
            design_tech_intersections(DETAIL))

    def test_subjects_not_in_table(self):
        self.assertNotIn("design_subjects", self._rows()[0])

    def test_always_empty_tech_labels_stay_dropped(self):
        """空欄比沒有欄更糟：讀者會以為「這些申請人沒有技術主題」。"""
        self.assertNotIn("tech_labels", self._rows()[0])


class TimelineTableMatchesChartTests(unittest.TestCase):
    """4. 主題演進的表＝主題 × 年（與圖同一份），不是主題統計表。"""

    TY = [
        {"label": "拉繩滑雪模擬機構", "application_year": 2021, "patent_count": 3},
        {"label": "拉繩滑雪模擬機構", "application_year": 2023, "patent_count": 7},
        {"label": "風磁複合阻力裝置", "application_year": 2022, "patent_count": 11},
    ]

    def test_pivot_shape_is_topic_by_year(self):
        rows = chart_runner.pivot_year_matrix(self.TY, "label")
        first = rows[0]
        self.assertIn("label", first)
        self.assertIn("2021", first)
        self.assertIn("total", first)
        self.assertNotIn("applicant_count", first, "不得再是主題統計表的欄")


class ColumnUnitLabelTests(unittest.TestCase):
    """5. 單位要標清楚；內部 key 不得上表頭。"""

    def test_granted_pending_labelled_with_unit(self):
        label = chart_runner.DATA_COLUMN_LABELS.get("granted_pending", "")
        self.assertTrue(label, "granted_pending 沒有中文欄名，表頭會印英文 key")
        self.assertIn("件", label, f"沒標單位：{label}")

    def test_kind_summary_labelled_with_unit(self):
        self.assertIn("件", chart_runner.DATA_COLUMN_LABELS.get("kind_summary", ""))


if __name__ == "__main__":
    unittest.main()
