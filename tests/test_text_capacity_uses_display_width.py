"""版面容量估算要用**顯示寬度**，不是字數（A1，2026-08-09）。

## 現象

實機 p7 的要點橫幅右欄只有一行、下方一大片空白，第 3 條要點卻被判定放不下
而整條丟棄（`points_dropped`）。加高橫幅 27% 之後仍然丟——因為**不是空間不夠**。

## 根因

`_text_capacity` 用 `len(text)` 數字數估行數，但中文字寬約 1 em、半形英數只有
`ALNUM_EM_WIDTH`（0.62 em）。要點裡的「美國玩家（扭矩、OXEFIT、NPD）各僅 US 1
件」這種中英混排，實際佔寬遠小於字數，於是行數被高估、內容被誤判成放不下。

⚠ 本專案**早就有** `_display_width` 這支正確的寬度函式（它自己的註解還記著
「兩處落點只改了一邊」的教訓），只是容量估算沒有用它——同一份知識有正確的
定義處，卻沒有被真正消費。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ppt_capacity", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_capacity", module)
    spec.loader.exec_module(module)
    return module


bp = _load_builder()
THEME = bp.Theme.load()


class LinesNeededTests(unittest.TestCase):
    """行數要依顯示寬度算。"""

    PER_LINE = 30   # 每行 30 em

    def test_pure_cjk_unchanged(self):
        """⚠ 對照組：全中文時 1 字＝1 em，結果必須與數字數時相同。"""
        text = "專" * 60
        self.assertEqual(bp._lines_needed(text, self.PER_LINE), 2)

    def test_alnum_counts_less_than_full_width(self):
        """同樣 45 個字元：中文 45 em 要 2 行，半形英數 27.9 em 只要 1 行。

        ⚠ 案例長度要落在兩側不同行數的區間才驗得出差異——首版用 60 字，
        兩者都是 2 行，測試等於沒在驗（自己踩到才發現）。
        """
        cjk = bp._lines_needed("專" * 45, self.PER_LINE)
        ascii_lines = bp._lines_needed("A" * 45, self.PER_LINE)
        self.assertEqual((cjk, ascii_lines), (2, 1),
                         "半形英數的行數估算不該與等量中文相同")

    def test_mixed_text_matches_display_width(self):
        """中英混排的行數＝顯示寬度 ÷ 每行容量（無條件進位）。"""
        import math

        text = "美國玩家（扭矩、OXEFIT、NPD）各僅 US 1 件，單國單件、布局點狀。"
        expected = math.ceil(bp._display_width(text) / self.PER_LINE)
        self.assertEqual(bp._lines_needed(text, self.PER_LINE), expected)

    def test_newline_segments_still_counted_separately(self):
        """⚠ 既有語意不得改變：每段至少佔一行，不足一行不合併。"""
        self.assertEqual(bp._lines_needed("甲\n乙\n丙", self.PER_LINE), 3)


class WidePointsFitTests(unittest.TestCase):
    """實機 p7 那三條要點，在 chart_wide 的橫幅裡必須全部放得下。"""

    POINTS = [
        ("", "帝瑪斯集團以中國為根：帝瑪斯 CN 11 件、曾晴 CN 10 件，幾乎全押中國市場。", "ink", False),
        ("", "台廠雙邊布局：曾晴、祺驊各在 TW 另有 2 件，兼顧兩岸。", "ink", False),
        ("", "美國玩家（扭矩、OXEFIT、NPD）各僅 US 1 件，單國單件、布局點狀。", "ink", False),
    ]

    def test_all_three_points_kept(self):
        g = THEME.geometry["chart_wide"]
        inset = g["band_inset_in"]
        columns = int(g["band_columns"])
        col_w = (g["band_width_in"] - inset * 2
                 - g["band_column_gap_in"] * (columns - 1)) / columns
        # 橫幅實際可用高：圖底緣往下到 band_bottom，扣掉標題與內距。
        band_top = g["image_top_in"] + g["image_height_in"] + g["band_gap_in"]
        text_height = (g["band_bottom_in"] - band_top
                       - g["band_text_top_offset_in"] - inset)
        kept = bp._trim_blocks(THEME, self.POINTS,
                               width_in=col_w, height_in=text_height * columns,
                               size_pt=THEME.size("point_text_pt"))
        self.assertEqual(len(kept), len(self.POINTS),
                         f"版面放得下卻被丟棄：只保留 {len(kept)}/{len(self.POINTS)} 條")



class PunctuationWidthTests(unittest.TestCase):
    """全形標點的顯示寬度要低於一個全形字（A1 收尾）。

    修好英數寬度後，實機 p7 仍差**一行**：第 2 條要點估 27.3 em、每行容量
    25.8 em，於是被算成兩行——但實機只用了一行。差距全在全形標點：那條有
    6 個「：、，。」，排版時右半是空白、可壓縮（PowerPoint 的標點擠壓），
    實際佔寬明顯不到一個全形字。

    ⚠ 只調標點，不動中文與英數：那兩個係數是 2026-08-03 以實測像素校準過的
    （英數 0.55→0.62 那次），沒有新的量測就不該碰。
    """

    def test_punctuation_narrower_than_han(self):
        for mark in "：、，。；（）":
            with self.subTest(mark=mark):
                self.assertLess(bp._display_width(mark), bp._display_width("專"),
                                f"{mark} 不該與漢字等寬")

    def test_han_and_alnum_unchanged(self):
        """⚠ 對照組：漢字與英數的既有係數不得被動到。"""
        self.assertEqual(bp._display_width("專"), 1.0)
        self.assertAlmostEqual(bp._display_width("A"), bp.ALNUM_EM_WIDTH, places=6)

    def test_both_implementations_stay_in_sync(self):
        """兩處字寬估算必須同值——只改一邊就是下一個 bug（本專案第 8 次）。"""
        from backend.app.reports import chart_runner as cr

        for text in ("台廠雙邊布局：曾晴、祺驊各在 TW 另有 2 件，兼顧兩岸。",
                     "A63B-0022", "121754861", "（廈門）健身器材"):
            with self.subTest(text=text):
                self.assertAlmostEqual(bp._display_width(text), cr._display_width(text),
                                       places=6, msg="兩處字寬估算不一致")


if __name__ == "__main__":
    unittest.main()
