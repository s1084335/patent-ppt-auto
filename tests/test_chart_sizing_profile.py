"""chart_sizing 兩份 profile 的契約測試（2026-08-03 定案、2026-08-04 落地）。

## 為什麼需要這支

網頁與 PPT 對圖表尺寸／字級的約束不同（PPT 圖框 4.32in、字要 ≥12pt；
網頁滿寬可捲動）。2026-08-03 為 PPT 調年度矩陣，連帶弄壞三張報表區塊的圖
（年份黏串、泡泡互撞、註記壓長條）——三個 bug 全來自「寫死的位置參數配上
改動的尺寸」。使用者定案：只把會衝突的參數拆成 `chart_sizing.PPT` / `WEB`
兩份 profile，版面邏輯共用。

## 使用規則（測試在守的契約）

- 🔴 **修 PPT 尺寸／字級 → 只改 `chart_sizing.PPT`**，不得回頭在
  `chart_runner` 寫死數字——本測試逐欄斷言 11 個常數必須綁 profile。
- ⚠ **位置參數必須由尺寸推導、不得寫死**（標籤位數、泡泡半徑、註記位置
  一律經 `chart_font_px`／`solve_chart_font` 反推）。兩份尺寸配上寫死的
  位置＝壞兩倍，不是壞一半——2026-08-03 三個 bug 的教訓。
- `WEB` 目前與 `PPT` 同值（兩端共用同一張 SVG）；日後網頁分流時，
  改掉本檔的同值斷言並在渲染端分流即可，欄位已備好。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner as cr
from backend.app.reports import chart_sizing


class ChartRunnerBindsWebProfileTests(unittest.TestCase):
    """chart_runner 的 11 個尺寸常數必須逐欄等於 chart_sizing.WEB。

    🔴 2026-08-12 契約更新（unify-chart-source 使用者定案）：綁定由 PPT 翻轉為
    **WEB**——每張圖只產一份 WEB 尺寸的 SVG，PPT 預放大退場、簡報端自行 refit。
    「常數必須綁 profile、不得寫死」的守門不變，變的只是綁哪一份。
    """

    def test_constants_match_web_profile(self) -> None:
        ppt = chart_sizing.WEB
        # (chart_runner 常數名, PPT profile 欄位名) 逐欄對照
        pairs = [
            ("CHART_CANVAS_WIDTH", "canvas_width"),
            ("CHART_CANVAS_MAX_HEIGHT", "canvas_max_height"),
            ("CHART_DATA_TARGET_PT", "data_target_pt"),
            ("CHART_NOTE_TARGET_PT", "note_target_pt"),
            ("CHART_ROW_HEIGHT", "row_height"),
            ("CHART_YEAR_WINDOW", "year_window"),
            ("BAR_HEIGHT_PX", "bar_height"),
            ("BUBBLE_MIN_RADIUS_PX", "bubble_min_radius"),
            ("CHART_HERO_FRAME_IN", "hero_frame_in"),
            ("CHART_WIDE_FRAME_IN", "wide_frame_in"),
            ("WIDE_CHART_ASPECT_MIN", "wide_aspect_min"),
        ]
        self.assertEqual(len(pairs), 11)
        for const_name, field_name in pairs:
            with self.subTest(const=const_name):
                self.assertEqual(
                    getattr(cr, const_name),
                    getattr(ppt, field_name),
                    f"chart_runner.{const_name} 必須綁 chart_sizing.WEB.{field_name}，"
                    "不得在 chart_runner 寫死數字",
                )


class WebProfileMirrorsPptTests(unittest.TestCase):
    """WEB profile 目前與 PPT 同值（單一輸出、兩端共用同一張 SVG）。

    日後網頁要走自己的尺寸時：改 chart_sizing.WEB、渲染端分流，
    並把這條斷言改成兩份 profile 各自的契約——屆時本測試「應該」紅。
    """

    def test_web_has_own_sizing_since_p3(self) -> None:
        """🔴 2026-08-07 契約更新（P3 separate-web-and-ppt-chart-profiles）：
        WEB 不再與 PPT 同值——網頁滿寬可捲、只縮一次，畫布更寬、字級目標較低。
        ⚠ 只有**尺寸與字級**分開；排序、配色、註記內容、版面邏輯仍共用
        （Non-goals 明列不建第二套 engine）。"""
        self.assertNotEqual(chart_sizing.WEB, chart_sizing.PPT)
        self.assertGreater(chart_sizing.WEB.canvas_width, chart_sizing.PPT.canvas_width)
        self.assertLess(chart_sizing.WEB.data_target_pt, chart_sizing.PPT.data_target_pt)
        # 版面邏輯共用的證據：非尺寸字級欄位必須相同。
        for field in ("year_window", "wide_aspect_min", "hero_frame_in", "wide_frame_in"):
            with self.subTest(field=field):
                self.assertEqual(getattr(chart_sizing.WEB, field),
                                 getattr(chart_sizing.PPT, field))


if __name__ == "__main__":
    unittest.main()
