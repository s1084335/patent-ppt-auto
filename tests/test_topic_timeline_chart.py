"""主題演進圖（2026-08-11 使用者裁決版：只做技術通道、主題×年泡泡矩陣）。

## 沿革

- 2026-08-10：新增主題×時間圖（早期 vs 近期雙條），兩通道各一張。
- 2026-08-10 深夜裁決：技術通道改**主題×年泡泡矩陣**（稀疏小整數泡泡最適、
  與申請人年度矩陣同讀法），功效暫維持雙條。
- 2026-08-11 裁決：「功效＝早期 vs 近期雙條**不要有**，主題演進就只做技術」
  「主題統計表（技術／功效），時間狀態拿掉」。
  → 雙條渲染函式 `render_topic_timeline_chart` 退場（唯一消費者消失）；
  `status`（技術狀態）自統計表顯示欄移除（功效的主展示是機會四象限，
  演進資訊由技術通道的泡泡矩陣承載）。

本檔守兩件事：聚合純函式 `topic_year_rows` 的正確性、
「雙條已死、狀態欄已藏」的契約不復活。
"""
from __future__ import annotations

import unittest


class TopicYearRowsTests(unittest.TestCase):
    """技術通道演進泡泡矩陣的**聚合純函式**：assignments×patents → 主題×年列。

    渲染複用 `year_bubble_matrix_layout`＋`render_year_bubble_matrix_chart`
    （申請人年度矩陣同一支），不另寫泡泡渲染器。
    """

    TOPICS = [
        {"topic_code": "T001", "label": "主題甲", "source_field": "tech_src"},
        {"topic_code": "T002", "label": "主題乙", "source_field": "tech_src"},
        {"topic_code": "T001", "label": "功效丙", "source_field": "eff_src"},
    ]
    ASSIGNMENTS = [
        {"topic_code": "T001", "patent_id": 1, "source_field": "tech_src"},
        {"topic_code": "T001", "patent_id": 2, "source_field": "tech_src"},
        {"topic_code": "T002", "patent_id": 3, "source_field": "tech_src"},
        # 同 code 不同通道：不得混進技術通道（兩通道各自從 T001 編號）
        {"topic_code": "T001", "patent_id": 4, "source_field": "eff_src"},
    ]
    PATENTS = {1: {"application_year": 2022}, 2: {"application_year": 2022},
               3: {"application_year": 2024}, 4: {"application_year": 2022}}

    def _rows(self, **kw):
        from backend.app.reports.chart_runner import topic_year_rows

        return topic_year_rows(
            kw.get("topics", self.TOPICS), kw.get("assignments", self.ASSIGNMENTS),
            kw.get("patents", self.PATENTS), source_field=kw.get("source_field", "tech_src"))

    def test_counts_grouped_by_topic_and_year(self):
        rows = self._rows()
        self.assertIn({"label": "主題甲", "application_year": 2022, "patent_count": 2}, rows)
        self.assertIn({"label": "主題乙", "application_year": 2024, "patent_count": 1}, rows)

    def test_other_channel_not_mixed_in(self):
        """⚠ 兩通道各自從 T001 編號——功效的 T001 混進來會把主題甲灌成 3 件。"""
        rows = self._rows()
        total = sum(r["patent_count"] for r in rows if r["label"] == "主題甲")
        self.assertEqual(total, 2)

    def test_missing_year_is_skipped_not_crash(self):
        """專利缺申請年（資料不全）＝略過該件，不得炸也不得記成 0 年。"""
        patents = {**self.PATENTS, 1: {}}
        rows = self._rows(patents=patents)
        self.assertEqual(
            sum(r["patent_count"] for r in rows if r["label"] == "主題甲"), 1)

    def test_empty_inputs_give_empty_rows(self):
        self.assertEqual(self._rows(assignments=[]), [])


class RetiredContractTests(unittest.TestCase):
    """🔴 2026-08-11 裁決的兩條「不復活」契約。"""

    def test_paired_timeline_renderer_is_gone(self):
        """雙條演進渲染器已退場——留著就是沒有消費者的死碼，會與泡泡版各自演進。"""
        from backend.app.reports import chart_runner

        self.assertFalse(hasattr(chart_runner, "render_topic_timeline_chart"),
                         "render_topic_timeline_chart 應隨「演進只做技術（泡泡）」退場")

    def test_status_column_hidden_from_topic_table(self):
        """「時間狀態拿掉」＝status 進顯示排除欄、不再佔顯示優先序。

        ⚠ 只藏**顯示**：rows 仍帶 status 供下游驗證（同 recent_assignee_count 慣例）。
        """
        from backend.app.reports.chart_runner import (
            DATA_TABLE_EXCLUDED_COLUMNS,
            DATA_TABLE_PRIORITY_COLUMNS,
        )

        excluded = DATA_TABLE_EXCLUDED_COLUMNS["cluster_topic_table"]
        self.assertIn("status", excluded, "技術狀態欄應自統計表顯示移除")
        priority = DATA_TABLE_PRIORITY_COLUMNS["cluster_topic_table"]
        self.assertNotIn("status", priority)
        # tech_means 同場清理：2026-08-10 裁決「不加欄，手段寫在解讀區」——
        # rows 從來沒有這個鍵，留在優先序是死鍵。
        self.assertNotIn("tech_means", priority)


if __name__ == "__main__":
    unittest.main()
