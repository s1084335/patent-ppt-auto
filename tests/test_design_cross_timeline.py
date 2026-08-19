"""技術交叉表改「逐家時序」＋策略矩陣兩欄（2026-08-18 使用者定案，方案 A）。

## 為什麼改

原表把設計清單與技術代表案並排，「交叉」是假的——**沒有任何欄位表達兩者的
關係**。實資料裡關係很清楚：

    廈門帝瑪斯   設計 2019, 2022  │ 技術 2020×4, 2022×2, 2024×5  → 設計先行
    廈門康樂佳   設計 2024        │ 技術 2024                    → 同年同步
    澳瑞特體育   設計 2015        │ 技術 2015                    → 同年同步

後兩家還是同一個產品的雙重保護（`Ski machine (K7528)` ／
`Skiing machine with adjustable wind resistance`）。這才是這張表該答的事。

## 兩個設計取捨

- **佈局順序是算術不是判斷**：比較首次申請年而已。⚠ 用詞守在事實層
  （設計先行 N 年／同年同步／技術先行 N 年），不寫「產品化訊號」那種超譯。
- **保護標的不進表格**：改由 CLI 讀 `patents."文獻備註"` 寫進解讀
  ——沿用 2026-08-10 定案（資料層給 `patent_ids`，不預先算好餵過去，
  否則 CLI 無法追問、無法深入）。所以列裡帶隱藏的 `design_patent_ids`。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner
from backend.app.reports.content_blocks import (
    design_protection_strategy,
    design_tech_intersections,
)

DETAIL = [
    # 帝瑪斯：設計 2019／2022，技術 2020／2024 → 設計先行 1 年
    {"patent_id": 134, "patent_type": "P", "document_kind": "S",
     "application_year": 2019, "applicant_display_name": "廈門帝瑪斯健康科技",
     "title": "Fan skiing training ware", "主權項": ""},
    {"patent_id": 140, "patent_type": "P", "document_kind": "S",
     "application_year": 2022, "applicant_display_name": "廈門帝瑪斯健康科技",
     "title": "Skiing trainer", "主權項": ""},
    {"patent_id": 88, "patent_type": "P", "document_kind": "A",
     "application_year": 2020, "applicant_display_name": "廈門帝瑪斯健康科技",
     "title": "阻力裝置", "主權項": "一種阻力裝置…"},
    {"patent_id": 89, "patent_type": "P", "document_kind": "A",
     "application_year": 2024, "applicant_display_name": "廈門帝瑪斯健康科技",
     "title": "自鎖訓練裝置", "主權項": "一種自鎖…"},
    # 康樂佳：同年
    {"patent_id": 109, "patent_type": "P", "document_kind": "S",
     "application_year": 2024, "applicant_display_name": "廈門康樂佳運動器材",
     "title": "Ski machine (K7528)", "主權項": ""},
    {"patent_id": 110, "patent_type": "P", "document_kind": "A",
     "application_year": 2024, "applicant_display_name": "廈門康樂佳運動器材",
     "title": "Skiing machine with adjustable wind resistance",
     "主權項": "一種可調風阻…"},
    # 只走設計：不進交叉表，但要進策略矩陣
    {"patent_id": 129, "patent_type": "P", "document_kind": "S",
     "application_year": 2021, "applicant_display_name": "Zhou Zheng",
     "title": "Body-building skiing machine", "主權項": ""},
]


class CrossTableIsTimelineTests(unittest.TestCase):
    def _rows(self):
        return chart_runner.design_intersection_table_rows(
            design_tech_intersections(DETAIL))

    def _by_applicant(self):
        return {r["applicant"]: r for r in self._rows()}

    def test_columns_are_four_plus_hidden_ids(self):
        row = self._rows()[0]
        self.assertEqual(
            list(row),
            ["applicant", "design_summary", "tech_summary", "filing_order",
             "design_patent_ids"])

    def test_counts_and_years_in_summary(self):
        """⚠ 恰兩個年份要**列舉**不能寫區間：`2020–2024` 會讓人以為中間年份
        也有件數。三個以上才用區間（否則列舉會太長）。"""
        d = self._by_applicant()["廈門帝瑪斯健康科技"]
        self.assertEqual(d["design_summary"], "2 件（2019、2022）")
        self.assertEqual(d["tech_summary"], "2 件（2020、2024）")

    def test_three_or_more_years_use_span(self):
        rows = design_tech_intersections(DETAIL + [
            {"patent_id": 90, "patent_type": "P", "document_kind": "A",
             "application_year": 2022, "applicant_display_name": "廈門帝瑪斯健康科技",
             "title": "第三件技術案", "主權項": "一種…"}])
        d = {r["applicant"]: r for r
             in chart_runner.design_intersection_table_rows(rows)}
        self.assertEqual(d["廈門帝瑪斯健康科技"]["tech_summary"], "3 件（2020–2024）")

    def test_filing_order_design_first(self):
        self.assertEqual(
            self._by_applicant()["廈門帝瑪斯健康科技"]["filing_order"], "設計先行 1 年")

    def test_filing_order_same_year(self):
        self.assertEqual(
            self._by_applicant()["廈門康樂佳運動器材"]["filing_order"], "同年同步")

    def test_design_only_applicant_absent(self):
        """只走設計者沒有交叉可言，不進這張表（範圍維持現狀）。"""
        self.assertNotIn("Zhou Zheng", self._by_applicant())

    def test_patent_ids_carried_for_cli(self):
        """⚠ 保護標的由 CLI 讀文獻備註自行撰寫，資料層只給 id。"""
        d = self._by_applicant()["廈門帝瑪斯健康科技"]
        self.assertEqual(sorted(d["design_patent_ids"]), [134, 140])

    def test_ids_hidden_from_table(self):
        hidden = chart_runner.DATA_TABLE_EXCLUDED_COLUMNS.get(
            "design_protection_detail", ())
        self.assertIn("design_patent_ids", hidden,
                      "內部識別碼會直接印在表上")

    def test_subjects_column_gone(self):
        self.assertNotIn("design_subjects", self._rows()[0])

    def test_all_visible_columns_labelled(self):
        hidden = set(chart_runner.DATA_TABLE_EXCLUDED_COLUMNS.get(
            "design_protection_detail", ()))
        for col in self._rows()[0]:
            if col in hidden:
                continue
            with self.subTest(col=col):
                self.assertIn(col, chart_runner.DATA_COLUMN_LABELS)


class StrategyMatrixTwoAxisTests(unittest.TestCase):
    """策略矩陣拿掉「技術+設計」。

    ⚠ 理由不只是「多餘」：`design_protection_strategy` 只收有設計案的申請人
    （`if not designs: continue`），所以第三欄**恆等於前兩欄相加**，
    永遠不會出現只走技術那一類。策略改由「技術欄是否為 0」直接讀出。
    """

    def _rows(self):
        return chart_runner.design_strategy_matrix_rows(
            design_protection_strategy(DETAIL))

    def test_only_two_axes(self):
        self.assertEqual(chart_runner.DESIGN_STRATEGY_AXIS, ("技術", "設計"))
        self.assertEqual({r["strategy_axis"] for r in self._rows()},
                         {"技術", "設計"})

    def test_no_total_column(self):
        for r in self._rows():
            self.assertNotEqual(r["strategy_axis"], "技術+設計")

    def test_design_only_shows_zero_tech(self):
        by = {(r["applicant"], r["strategy_axis"]): r["patent_count"]
              for r in self._rows()}
        self.assertEqual(by[("Zhou Zheng", "技術")], 0)
        self.assertEqual(by[("Zhou Zheng", "設計")], 1)


if __name__ == "__main__":
    unittest.main()
