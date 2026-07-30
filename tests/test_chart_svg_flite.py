"""F-lite：圖表 SVG 只換配色與字體，圖型邏輯不動（2026-07-31 使用者核准）。

## 背景

PPT 版面已換 Slidesgo 系（深藍 00094A／皇家藍 516CEE／亮藍 006DF5），但圖表
SVG 還是舊配色（綠 0F766E／灰藍 64748B）且**沒設 font-family**——瀏覽器與
PowerPoint 轉圖都退回襯線字，新畫框裝舊畫。

使用者定案：「配色與字體可以改」「圖表一樣用我們產的」——**只換皮，不動圖型**。
年度矩陣維持熱圖表格；不加新圖型；不改任何數據計算。

⚠ 色票對照 skill 的 theme.json（同一組 Slidesgo 色），但 chart_runner 是引擎端、
theme.json 是 skill 端——**值以常數對齊，不做 runtime 依賴**（引擎不 import skill；
容器打包順序也不允許反向依賴）。改色改兩處是已知取捨，本測試釘住兩邊一致。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.reports import chart_runner

THEME = json.loads(
    (Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "theme.json")
    .read_text(encoding="utf-8")
)


class SvgPaletteTests(unittest.TestCase):
    """🔴 SVG 色票對齊 Slidesgo（theme.json 同組色）。"""

    def test_palette_matches_theme(self):
        color = THEME["color"]
        self.assertEqual(chart_runner.COLOR_APPLICATION.lstrip("#").upper(), color["blue"],
                         "申請線應用主題藍")
        self.assertEqual(chart_runner.COLOR_TEXT.lstrip("#").upper(), color["navy"],
                         "標題／主文字應用深藍")
        self.assertEqual(chart_runner.COLOR_BAR.lstrip("#").upper(), color["blue"],
                         "長條主色應用主題藍（舊綠 0F766E 退場）")
        self.assertEqual(chart_runner.COLOR_BAR_ALT.lstrip("#").upper(), color["muted"],
                         "長條次色應用灰藍")

    def test_old_green_gone(self):
        src = Path(chart_runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("0F766E", src, "舊綠色仍在")


class SvgFontTests(unittest.TestCase):
    """🔴 SVG 要宣告字體（微軟正黑體）——沒宣告就是襯線的來源。"""

    def test_svg_declares_font(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.svg"
            chart_runner.render_line_chart(path, "測試標題",
                                           [{"application_year": 2020, "patent_count": 3}], [])
            svg = path.read_text(encoding="utf-8")
        self.assertIn("Microsoft JhengHei", svg, "SVG 未宣告微軟正黑體")


if __name__ == "__main__":
    unittest.main()
