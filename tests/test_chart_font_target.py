"""圖表文字的**最終顯示大小**要達標——單一來源版契約。

## 沿革（本檔兩次契約變更都記在這）

- 2026-08-04 使用者定案（PPT 時代）：資料 14pt／註記 12pt，字級由畫布尺寸
  反推補償 PPT 圖框縮放——本檔原本逐畫布驗「縮進圖框後落在 14/12pt」。
- 2026-08-11 使用者定案：「HTML 的圖中文字都維持在 15」——web 不補償、
  資料與註記同級 15px。
- 🔴 2026-08-12（unify-chart-source）：**單一來源＝WEB 尺寸**，PPT 預放大
  退場（chart_scale ≡ 1.0），簡報端（deck skill）自行 refit 字級。
  「縮進圖框後達標」的舊斷言失去對象，本檔改鎖：**任何畫布反推出的字級
  都是同一個 15px**——這正是「全部圖表在頁面上同一字高」的使用者要求。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner as cr

#: 15px @96dpi ＝ 11.25pt（chart_sizing.WEB 的唯一定義值）。
_TARGET_PX = 15.0


class FontIsUniformTests(unittest.TestCase):
    """不同形狀的畫布（原 PPT 時代的實測樣本）反推字級必須全部相同。"""

    CANVASES = (
        ("一般圖", 949, 460),
        ("扁圖", 949, 214),
        ("機會象限", 1120, 629),
        ("年度矩陣", 942, 456),
        ("web 畫布", 1180, 560),
    )

    def test_data_text_is_15px_everywhere(self):
        for name, w, h in self.CANVASES:
            with self.subTest(name):
                px = cr.chart_font_px(w, h)
                # epsilon 1.005 沿用（2026-08-07 邊界餘裕教訓）：≥15px、溢出 ≤1%。
                self.assertGreaterEqual(px, _TARGET_PX, f"{name} 資料文字低於 15px")
                self.assertLessEqual(px, _TARGET_PX * 1.01, f"{name} 溢出逾 1%")

    def test_note_equals_data(self):
        """🔴 2026-08-11 契約反轉：註記**不再小一級**（使用者「都維持在 15」）。

        舊契約「註記比資料小、避免搶注意力」是 PPT 版面的取捨；網頁上
        全圖同字高才是定案。
        """
        for name, w, h in self.CANVASES:
            with self.subTest(name):
                self.assertEqual(
                    cr.chart_font_px(w, h, target_pt=cr.CHART_NOTE_TARGET_PT),
                    cr.chart_font_px(w, h))

    def test_scale_is_identity(self):
        """PPT 圖框補償退場——縮放比對任何畫布恆 1.0。"""
        for name, w, h in self.CANVASES:
            with self.subTest(name):
                self.assertEqual(cr.chart_scale(float(w), float(h)), 1.0)

    def test_targets_are_the_agreed_values(self):
        """15px＝11.25pt，資料與註記同值（2026-08-11／08-12 兩次定案的合成）。"""
        self.assertEqual(cr.CHART_DATA_TARGET_PT, 11.25)
        self.assertEqual(cr.CHART_NOTE_TARGET_PT, 11.25)


if __name__ == "__main__":
    unittest.main()
