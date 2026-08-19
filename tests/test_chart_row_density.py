"""橫條圖列密度：圖高隨資料、條間距固定、字級不受列數影響。

🔴 2026-08-19 使用者裁決（實機看 IPC/CPC 4 階／5 階分頁）：
「bar 間距縮小，文字同樣大小，圖不一定要撐那麼大張，根據資料變動」。

⚠ 本裁決**推翻** 2026-08-03 的 G-7／H-6「少列時撐開列高填滿畫布」，
   對應的 `test_few_rows_still_fill_the_frame` 已改寫為
   `test_canvas_height_tracks_row_count`（見 test_chart_sections.py）。
   推翻的依據不只是偏好——`chart_scale()` 自 2026-08-12（unify-chart-source）
   起**恆為 1.0**，PPT 二次縮放補償早已退場、簡報端改由 deck skill 逐圖 refit，
   「撐開是為了填滿 PPT 圖框」的前提已不存在。

實機量到的病徵（report_trial_20260819_122745）：
    列數  1    2    3     5    8    10
    列距  —    54   108   91   57   45      ← 毫無規律
    空白  —    36   90    73   39   27      ← 3 列那張是條高的 5 倍

⚠ 這幾支斷言的是**關係**不是絕對值：列距要跨列數一致、空白要與條高成比例、
   高度要隨列數線性成長。寫死「列距＝27」會在字級或 profile 一變就假紅，
   而且擋不住「所有列距都一樣地錯」。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.reports import chart_runner


def _render(n_rows: int) -> dict[str, object]:
    """畫 n 列橫條圖，從**產物**量回幾何值（不讀程式內部變數）。"""
    rows = [{"ipc_main_group_symbol": f"A63B-{i:03d}", "patent_count": 10 - i}
            for i in range(n_rows)]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bar.svg"
        chart_runner.render_bar_chart(path, "x", rows, "ipc_main_group_symbol")
        svg = path.read_text(encoding="utf-8")
    height = int(re.search(r'<svg[^>]*\bheight="(\d+)"', svg).group(1))
    bars = sorted(
        (float(y), float(h)) for y, h in
        re.findall(r'<rect[^>]*\by="([\d.]+)"[^>]*\bheight="([\d.]+)"', svg))
    bars = [b for b in bars if b[1] < height * 0.5]      # 濾掉整片白底
    sizes = sorted({float(x) for x in re.findall(r'font-size="([\d.]+)"', svg)})
    return {
        "height": height,
        "bar_h": bars[0][1] if bars else 0.0,
        "pitch": (bars[1][0] - bars[0][0]) if len(bars) >= 2 else None,
        "font_sizes": sizes,
    }


class BarChartRowDensityTests(unittest.TestCase):
    """橫條圖的列距／圖高／字級三項關係。"""

    COUNTS = (2, 3, 5, 8, 10)

    def test_row_pitch_is_constant_across_row_counts(self):
        """同一種圖，列距不得因為列數多寡而改變。

        ⚠ 這是使用者實際踩到的病徵：4 階（2 列）與 5 階（5 列）是同一張圖的
        兩個分頁，切換時條會跳位——列距 54 vs 91。
        """
        pitches = {n: _render(n)["pitch"] for n in self.COUNTS}
        distinct = {round(p) for p in pitches.values() if p is not None}
        self.assertEqual(
            len(distinct), 1,
            f"列距隨列數變動：{pitches}——同一種圖的不同分頁會條位不一致")

    def test_gap_between_bars_is_smaller_than_the_bar(self):
        """條間空白不得超過條高——超過就是「分那麼開」。

        ⚠ 用**比例**而非固定 px：條高改了空白要跟著改，寫死 px 會讓兩者脫鉤。
        """
        for n in self.COUNTS:
            with self.subTest(rows=n):
                m = _render(n)
                gap = m["pitch"] - m["bar_h"]
                self.assertLessEqual(
                    gap, m["bar_h"],
                    f"{n} 列：條高 {m['bar_h']}、條間空白 {gap}"
                    f"（{gap / m['bar_h']:.1f} 倍）")

    def test_canvas_height_tracks_row_count(self):
        """圖高隨資料量線性成長，不再撐開填滿固定畫布。

        判準＝相鄰列數的高度差為定值（＝列距）。⚠ 不斷言絕對高度：
        那會隨字級與 profile 變，而且「都一樣高」正是要擋的舊行為。
        """
        heights = {n: _render(n)["height"] for n in self.COUNTS}
        deltas = {
            (a, b): heights[b] - heights[a]
            for a, b in zip(self.COUNTS, self.COUNTS[1:])
        }
        per_row = {round(d / (b - a)) for (a, b), d in deltas.items()}
        self.assertEqual(
            len(per_row), 1,
            f"每多一列增加的高度不一致：{deltas}（各列數高度 {heights}）")
        self.assertGreater(
            heights[self.COUNTS[-1]], heights[self.COUNTS[0]],
            f"列數多的圖沒有比較高：{heights}——高度沒有隨資料變動")

    def test_font_size_is_not_affected_by_row_count(self):
        """字級不因列數（進而畫布高度）改變——使用者要求「文字同樣大小」。

        ⚠ 這一項容易被誤以為自動成立：`chart_font_px` 是
        `target_pt / chart_scale(width, height)`，**依賴畫布高度**。
        目前 `chart_scale` 恆回 1.0 才使它成立；哪天縮放補償回來，
        改矮畫布就會連帶縮字，這支會先紅。
        """
        by_count = {n: _render(n)["font_sizes"] for n in self.COUNTS}
        first = by_count[self.COUNTS[0]]
        for n in self.COUNTS[1:]:
            self.assertEqual(
                by_count[n], first,
                f"{n} 列的字級 {by_count[n]} 與 {self.COUNTS[0]} 列的 {first} 不同")


if __name__ == "__main__":
    unittest.main()
