"""報表種類頁三項呈現修正（2026-07-29 使用者定案 R1-R3）。

## R1 選單攤平

實機：選單出現孤立的「4 階·Subclass」「5 階·Main Group」「Top 10」「11-20」，
且 IPC 與 CPC 各有同名 variant，看起來就是重複。

根因＝`buildReportViewOptions` 見 `variants.length > 1` 就把每個 variant 攤成
獨立選項，且只用 `variant.label` 不帶 section 標題。

定案：**主選單只列大類（一個 section 一項），子項進內部分頁**。

## R2 表格欄名是英文 key、無欄邊界

前端三處 `Object.keys(rows[0])` 直接吐 `topic_code`／`patent_count`。
而**後端 `chart_runner.py` 早有完整中文對照表**（`patent_count → 專利件數`）
——第 4 個「同一資訊兩處落點」。

定案：後端隨 content 輸出 `column_labels`，前端查表；查無對照顯示原 key
（後端新增欄位不必改前端）。

## R3 圖太小

`.report-single-data`／`.report-single-chart` 各 `flex 1 1 46%`，圖被壓在半寬。
定案：**圖改滿寬、數據表移到圖下方**。

⚠ R4（共同申請人分析拆分）**不在本檔**——使用者定案分批：R1-R3 是純呈現、
改壞了一看就知道；R4 動統計數字、改壞了會產出看似合理但錯誤的數據，
驗收心智模式不同，故獨立為 `test_applicant_split.py`。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


def _js_function(html: str, name: str) -> str:
    m = re.search(
        r"^(async\s+)?function " + re.escape(name) + r"\([^)]*\) \{(.*?)^\}",
        html, re.S | re.M)
    assert m, f"找不到函式 {name}"
    return m.group(2)


class R1MenuCollapseTests(unittest.TestCase):
    """R1：主選單一個 section 一項，variant 進內部分頁。"""

    def test_options_one_per_section(self):
        """`buildReportViewOptions` 不得再依 variant 攤平。"""
        body = _js_function(INDEX_HTML.read_text(encoding="utf-8"),
                            "buildReportViewOptions")
        self.assertNotIn("variants.forEach", body,
                         "仍在攤平 variant——選單會出現孤立的「4 階·Subclass」等重複項")

    def test_variant_tabs_exist(self):
        """內容區要有 variant 分頁（沿用既有 report-source-tab 樣式，不另造）。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("reportVariantTabsHtml", html,
                      "缺 variant 分頁——子項移出選單後就沒有入口了")


class R2ColumnLabelsTests(unittest.TestCase):
    """R2：欄名走後端中文對照表，不再吐原始 key。"""

    def test_backend_exposes_column_labels(self):
        """content 端點要輸出 column_labels（唯一來源＝chart_runner 既有對照表）。"""
        src = (PROJECT_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("column_labels", src,
                      "後端沒輸出 column_labels，前端只能顯示英文 key")

    def test_label_source_is_chart_runner(self):
        """必須沿用 chart_runner 既有的對照表，不得在 main.py 另寫一份。"""
        src = (PROJECT_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        m = re.search(r"def _column_labels.*?(?=\ndef |\n@)", src, re.S)
        self.assertIsNotNone(m, "找不到 _column_labels")
        self.assertIn("chart_runner", m.group(0),
                      "沒沿用 chart_runner 的對照表——第 5 個兩處落點")

    def test_frontend_uses_labels(self):
        """前端表頭要查表；查無對照才退回原 key。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("columnLabel(", html, "前端沒有查表 helper")
        body = _js_function(html, "reportSingleHtml")
        self.assertIn("columnLabel(", body, "reportSingleHtml 的表頭沒查表")


class R3ChartFullWidthTests(unittest.TestCase):
    """R3：圖滿寬、表在下。"""

    def test_layout_is_stacked_not_side_by_side(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        m = re.search(r"\.report-single\s*\{([^}]*)\}", html)
        self.assertIsNotNone(m, "找不到 .report-single 樣式")
        block = m.group(1)
        self.assertIn("column", block,
                      ".report-single 應為上下排列（flex-direction: column）")

    def test_chart_no_longer_half_width(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        m = re.search(r"\.report-single-chart\s*\{([^}]*)\}", html)
        self.assertIsNotNone(m)
        self.assertNotIn("46%", m.group(1), "圖仍被壓在半寬")


if __name__ == "__main__":
    unittest.main()
