"""技術／功效切換要真的生效（2026-07-30 使用者實機回報「功效按不下去」）。

## 根因

`reportSingleHtml` 取通道的順序：

    const sourceField = (option && option.source_field) || exportPreview.clusterSourceField || ...
                         ↑ 優先

而 `option.source_field` 在建選單時就**寫死**成 `DEFAULT_TOPIC_SOURCE_FIELD`（技術）：

    options.push(Object.assign({ sectionIndex, source_field: DEFAULT_TOPIC_SOURCE_FIELD }, view))

⚠ 於是點「功效」→ `switchReportSourceField` 確實更新了
`exportPreview.clusterSourceField` → 但那個值**永遠被 option.source_field 蓋掉**，
畫面重繪後仍是技術。按鈕看起來沒反應。

⚠ 靜默失敗：按鈕 active 樣式可能切了、資料卻沒換，比完全沒反應更誤導。

## 定案

使用者點選的通道（`exportPreview.clusterSourceField`）**優先**；
`option.source_field` 只作為初始值 fallback。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


class SourceTabSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_user_choice_wins_over_option_default(self):
        """🔴 使用者點的通道要優先於 option 內寫死的預設值。"""
        self.assertNotIn(
            "const sourceField = (option && option.source_field) "
            "|| exportPreview.clusterSourceField",
            self.html,
            "option.source_field 仍優先，使用者點『功效』會被蓋回技術")

    def test_switch_updates_shared_state(self):
        """切換函式要更新共用狀態（renderReportViewer 讀得到的那個）。"""
        match = re.search(
            r"function switchReportSourceField\(.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 switchReportSourceField")
        body = match.group(0)
        self.assertIn("exportPreview.clusterSourceField", body)
        self.assertIn("renderReportViewer()", body, "切換後未重繪")

    def test_rows_filtered_by_active_source(self):
        """⚠ 列過濾要用**目前生效**的通道，不是 option 的固定值。"""
        match = re.search(
            r"function sectionForReportView\(.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match)
        self.assertIn("row.source_field === sourceField", match.group(0),
                      "未依 source_field 過濾列")


class EmptyChartLayoutTests(unittest.TestCase):
    """沒有圖表時，表格用整寬（2026-07-30 使用者定案）。

    使用者：「這邊沒有圖表，那表格就可以用左右邊」——主題統計表已改為
    純數據表（不再產 HTML 變體），右半邊顯示「尚無圖表」空著，
    表格卻被壓在 45% 寬，欄位擠成多行。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_full_width_class_exists(self):
        """要有「無圖表時表格整寬」的樣式。"""
        self.assertIn("report-single-nochart", self.html,
                      "缺無圖表時的整寬樣式")

    def test_applied_when_no_variants(self):
        """`reportSingleHtml` 要在沒有圖表時套用該樣式。"""
        match = re.search(r"function reportSingleHtml\(.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 reportSingleHtml")
        self.assertIn("report-single-nochart", match.group(0),
                      "未依有無圖表切換版面")


if __name__ == "__main__":
    unittest.main()
