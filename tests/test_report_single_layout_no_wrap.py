"""左數據右圖表不得被 gap 擠到換行（2026-07-29 使用者實機回報）。

## 問題

使用者：「其他報表，圖表還是大到跑去下面」「圖表大到自己跑去表格下面」。

## 根因

    .report-single       { display: flex; gap: 1rem; flex-wrap: wrap; }
    .report-single-data  { flex: 0 0 45%; }   ← 固定 45%
    .report-single-chart { flex: 1 1 55%; }   ← 基準 55%

⚠ **45% + 55% = 100%，再加 `gap: 1rem` 就超過容器寬度** → `flex-wrap: wrap`
把圖表換行到表格下方。

這是間歇性的：視窗寬時 1rem 佔比小、可能勉強塞得下；視窗一窄就換行，
所以「有時正常有時跑掉」，比穩定壞掉更難查。

## 修法

兩個 flex-basis 相加必須**留出 gap 的空間**。用 `calc()` 從基準扣掉各自分攤的 gap，
或改用 grid（欄寬由 grid 自行分配，gap 不佔子項寬度）。

⚠ 不能只把 `flex-wrap` 改成 `nowrap`：那會讓窄視窗下兩欄被硬擠到不可讀，
而 stacked 版型（年度矩陣）本來就靠 wrap 之外的 flex-direction 控制，不受影響。

## 2026-08-03 更新：版面改單欄，本測試改為條件式

使用者定案改成「圖滿寬、表格排下方」（`flex-direction: column`），
兩欄並排的 gap 擠行風險因此消失——實測 4 列扁圖在 55% 欄裡被縮到字剩 7.6px。

⚠ **測試不刪**：它守的是「兩欄並排時要留 gap」，只要日後改回並排就必須重新生效。
故改為**先判斷目前是不是單欄**，是就跳過寬度檢查、改驗單欄本身成立。
直接刪掉等於把 07-29 那次的教訓一起丟了。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


def _rule(selector: str) -> str:
    """取某個 CSS 選擇器的宣告區塊——**合併同名規則的全部出現**。

    ⚠ 原本只取第一個匹配。CSS 是層疊的，同一個選擇器可以出現多次、後者覆蓋前者：
    `.report-single` 第一次宣告 `display:flex`、第二次才加 `flex-direction: column`。
    只讀第一個會得到過期的樣貌（2026-08-03 實際踩到——判斷「是不是單欄」永遠回 False）。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    blocks = re.findall(
        r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", html, re.M)
    assert blocks, f"找不到 CSS 規則 {selector}"
    return " ".join(blocks)


def _is_single_column() -> bool:
    """目前是否為單欄版面（圖滿寬、表格排下方）。"""
    return "flex-direction: column" in _rule(".report-single")


class ReportSingleLayoutTests(unittest.TestCase):
    """左右兩欄的寬度總和不得超過容器（單欄版面下不適用，見檔頭）。"""

    def test_single_column_is_declared_when_bases_are_full(self):
        """🔴 兩欄都宣告 100% 時，容器**必須**是單欄——否則就是 07-29 的擠行 bug。"""
        combined = _rule(".report-single-data") + _rule(".report-single-chart")
        percents = [float(p) for p in
                    re.findall(r"flex:\s*[\d.]+\s+[\d.]+\s+([\d.]+)%", combined)]
        if percents and sum(percents) >= 100.0:
            self.assertTrue(
                _is_single_column(),
                f"兩欄 flex-basis 相加 {sum(percents)}% ≥ 100% 卻不是單欄版面——"
                "加上 gap 會超出容器，圖表被 flex-wrap 擠到表格下方（2026-07-29 實機回報）")

    def test_chart_comes_first_in_single_column(self):
        """單欄時圖要在表格上方——使用者要的是「圖滿寬」，不是「表格滿寬」。"""
        if not _is_single_column():
            self.skipTest("目前不是單欄版面")
        self.assertIn("order: -1", _rule(".report-single-chart"),
                      "單欄版面下圖表沒有排到表格前面")

    def test_basis_leaves_room_for_gap(self):
        """🔴 兩欄 flex-basis 相加 + gap 不得超過 100%。

        原本 45% + 55% = 100%，gap:1rem 直接把圖表擠到下一行。
        """
        if _is_single_column():
            self.skipTest("單欄版面沒有並排擠行問題（見檔頭 2026-08-03 更新）")
        data = _rule(".report-single-data")
        chart = _rule(".report-single-chart")
        percents = []
        for block in (data, chart):
            found = re.findall(r"flex:\s*[\d.]+\s+[\d.]+\s+([\d.]+)%", block)
            percents.extend(float(p) for p in found)
        if percents:
            self.assertLess(
                sum(percents), 100.0,
                f"兩欄 flex-basis 相加 {sum(percents)}% ≥ 100%，"
                "加上 gap 會超出容器，圖表被 flex-wrap 擠到表格下方")

    def test_gap_accounted_when_basis_is_full(self):
        """若仍用 45/55 這種相加滿版的寫法，必須用 calc() 扣掉 gap。"""
        if _is_single_column():
            self.skipTest("單欄版面沒有並排擠行問題（見檔頭 2026-08-03 更新）")
        data = _rule(".report-single-data")
        chart = _rule(".report-single-chart")
        combined = data + chart
        percents = [
            float(p) for p in
            re.findall(r"flex:\s*[\d.]+\s+[\d.]+\s+([\d.]+)%", combined)
        ]
        if percents and sum(percents) >= 100.0:
            self.assertIn(
                "calc(", combined,
                "flex-basis 相加已達 100%，需用 calc() 扣掉 gap 佔用的寬度")

    def test_stacked_variant_still_full_width(self):
        """⚠ 年度矩陣（stacked）維持整寬單欄，不受本次修正影響。

        使用者定「年度矩陣可以和其他種類報表的版面不同」。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(".report-single-stacked { flex-direction: column; }", html,
                      "stacked 版型被改動——年度矩陣需要整個寬度")
        stacked = re.search(
            r"\.report-single-stacked \.report-single-data,\s*"
            r"\.report-single-stacked \.report-single-chart \{([^}]*)\}", html)
        self.assertIsNotNone(stacked, "找不到 stacked 子項規則")
        self.assertIn("100%", stacked.group(1), "stacked 子項應為整寬")

    def test_chart_image_still_scales(self):
        """圖表本身仍需 max-width:100%，否則寬 SVG 會撐破欄位。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(
            html,
            r"\.report-single-chart img,\s*\.report-single-chart svg \{[^}]*max-width:\s*100%",
            "圖表未限制最大寬度，寬 SVG 會撐破欄位")


if __name__ == "__main__":
    unittest.main()
