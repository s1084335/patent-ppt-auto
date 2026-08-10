"""圖表文字的**最終顯示大小**要達標（2026-08-04 使用者定案）。

## 為什麼需要這支

SVG 的字級寫死 px，圖再被縮進 PPT 圖框——**縮放比由畫布尺寸決定**，
所以同一個 18px 在不同頁面會變成完全不同的大小。實測第五輪 PPT：

| 圖 | 畫布 | 縮放 | 18px 的最終大小 |
|---|---|---|---|
| 一般圖 | 949×460 | 0.900 | 12.2pt |
| 扁圖（IPC/CPC L4） | 949×214 | 1.227 | 16.6pt |
| 機會象限 | 1120×629 | 0.763 | 10.3pt |
| 象限板 chip（12px） | 1120×629 | 0.763 | **6.9pt** |

🔴 使用者定案：**資料文字一律 14pt、註記／圖例一律 12pt**
（「超過的降下來，不夠的要調上去」）。

⚠ 所以字級不能是寫死的數字，必須由畫布尺寸**反推**：
`SVG字級px = 目標pt ÷ 0.75 ÷ 縮放`。新增任何圖都自動達標，不必逐張調。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.reports import chart_runner as cr

_THEME = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt" / "theme.json"


# ⚠ FrameConstantsMatchThemeTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


class FontReachesTargetTests(unittest.TestCase):
    """實測第五輪那批畫布，反推的字級要真的落在目標值。"""

    CANVASES = [
        ("一般圖", 949, 460),
        ("扁圖", 949, 214),
        ("機會象限", 1120, 629),
        ("年度矩陣", 942, 456),
    ]

    @staticmethod
    def _final_pt(width_px: int, height_px: int, font_px: float) -> float:
        """這個字級縮進 PPT 之後的實際 pt。"""
        w_in, h_in = width_px / 96.0, height_px / 96.0
        frame = (cr.CHART_WIDE_FRAME_IN if w_in / h_in >= cr.WIDE_CHART_ASPECT_MIN
                 else cr.CHART_HERO_FRAME_IN)
        scale = min(frame[0] / w_in, frame[1] / h_in)
        return font_px * 0.75 * scale

    def test_data_text_lands_on_14pt(self):
        for name, w, h in self.CANVASES:
            with self.subTest(name):
                px = cr.chart_font_px(w, h)
                # 🔴 2026-08-07 契約更新：chart_font_px 加 1.005 epsilon（實測 4 欄
                # 窄畫布縮放後 11.9957pt 跌破下限）。落點由「精確命中」改為
                # 「**不低於目標**、溢出 ≤1%」——下限才是使用者可感知的硬約束。
                final = self._final_pt(w, h, px)
                self.assertGreaterEqual(final, cr.CHART_DATA_TARGET_PT,
                                        f"{name} 資料文字低於 14pt")
                self.assertLessEqual(final, cr.CHART_DATA_TARGET_PT * 1.01,
                                     f"{name} 資料文字溢出逾 1%")

    def test_note_text_lands_on_12pt(self):
        for name, w, h in self.CANVASES:
            with self.subTest(name):
                px = cr.chart_font_px(w, h, target_pt=cr.CHART_NOTE_TARGET_PT)
                final = self._final_pt(w, h, px)
                self.assertGreaterEqual(final, cr.CHART_NOTE_TARGET_PT,
                                        f"{name} 註記低於 12pt")
                self.assertLessEqual(final, cr.CHART_NOTE_TARGET_PT * 1.01,
                                     f"{name} 註記溢出逾 1%")

    def test_note_is_smaller_than_data(self):
        """⚠ 註記要比資料小一級——兩者一樣大時，備註會跟內容搶注意力。"""
        for name, w, h in self.CANVASES:
            with self.subTest(name):
                self.assertLess(cr.chart_font_px(w, h, target_pt=cr.CHART_NOTE_TARGET_PT),
                                cr.chart_font_px(w, h))

    def test_targets_are_the_agreed_values(self):
        self.assertEqual(cr.CHART_DATA_TARGET_PT, 14.0)
        self.assertEqual(cr.CHART_NOTE_TARGET_PT, 12.0)


if __name__ == "__main__":
    unittest.main()
