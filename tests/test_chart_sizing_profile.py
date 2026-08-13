"""chart_sizing 的契約測試（2026-08-03 定案、2026-08-04 落地、2026-08-12 收斂為單一 profile）。

## 為什麼需要這支

2026-08-03 為 PPT 調年度矩陣，連帶弄壞三張報表區塊的圖（年份黏串、泡泡互撞、
註記壓長條）——三個 bug 全來自「寫死的位置參數配上改動的尺寸」。當時的解法是
把會衝突的參數拆成 `PPT`／`WEB` 兩份 profile。

`unify-chart-source`（RPT-010，2026-08-12 驗收）改為**圖表單一來源**後，
`PPT` profile 失去所有消費者，於 `restructure-html-report-export` 刪除。
本檔的守門不變、範圍縮小：常數必須綁唯一 profile，不得寫死。

## 使用規則（測試在守的契約）

- 🔴 **改尺寸／字級 → 只改 `chart_sizing.WEB`**，不得回頭在 `chart_runner`
  寫死數字——本測試逐欄斷言 8 個常數必須綁 profile。
- ⚠ **位置參數必須由尺寸推導、不得寫死**（標籤位數、泡泡半徑、註記位置
  一律經 `chart_font_px`／`solve_chart_font` 反推）。尺寸配上寫死的位置
  ＝壞兩倍，不是壞一半——2026-08-03 三個 bug 的教訓。
- **只能有一份 profile**：第二份參數沒有消費者時就是假知識（見 SingleProfileTests）。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner as cr
from backend.app.reports import chart_sizing


class ChartRunnerBindsWebProfileTests(unittest.TestCase):
    """chart_runner 的尺寸常數必須逐欄等於 chart_sizing.WEB。

    🔴 2026-08-12 契約更新兩次：
    ① unify-chart-source——綁定由 PPT 翻轉為 **WEB**（單一來源，簡報端自行 refit）。
    ② restructure-html-report-export——PPT profile 刪除，**11 → 8 個常數**
      （三個 PPT 圖框常數隨之移除，它們是已移除的 build_ppt 的數字）。
    「常數必須綁 profile、不得寫死」的守門始終不變。
    """

    def test_constants_match_web_profile(self) -> None:
        web = chart_sizing.WEB
        # (chart_runner 常數名, WEB profile 欄位名) 逐欄對照
        pairs = [
            ("CHART_CANVAS_WIDTH", "canvas_width"),
            ("CHART_CANVAS_MAX_HEIGHT", "canvas_max_height"),
            ("CHART_DATA_TARGET_PT", "data_target_pt"),
            ("CHART_NOTE_TARGET_PT", "note_target_pt"),
            ("CHART_ROW_HEIGHT", "row_height"),
            ("CHART_YEAR_WINDOW", "year_window"),
            ("BAR_HEIGHT_PX", "bar_height"),
            ("BUBBLE_MIN_RADIUS_PX", "bubble_min_radius"),
        ]
        self.assertEqual(len(pairs), 8)
        for const_name, field_name in pairs:
            with self.subTest(const=const_name):
                self.assertEqual(
                    getattr(cr, const_name),
                    getattr(web, field_name),
                    f"chart_runner.{const_name} 必須綁 chart_sizing.WEB.{field_name}，"
                    "不得在 chart_runner 寫死數字",
                )

    def test_removed_ppt_frame_constants_stay_gone(self) -> None:
        """三個 PPT 圖框常數已隨 profile 刪除；復活代表假知識又長回來。"""
        for const_name in ("CHART_HERO_FRAME_IN", "CHART_WIDE_FRAME_IN",
                           "WIDE_CHART_ASPECT_MIN"):
            with self.subTest(const=const_name):
                self.assertFalse(hasattr(cr, const_name))


class SingleProfileTests(unittest.TestCase):
    """🔴 2026-08-12 契約更新（restructure-html-report-export）：**WEB 是唯一 profile**。

    前身為 `WebProfileMirrorsPptTests`，斷言 WEB 與 PPT 兩份 profile 的差異。
    契約為何改：`PPT` profile **已無任何消費者**——`unify-chart-source`（RPT-010）
    定案單一來源後，`chart_runner` 只 import `WEB`，全庫沒有第二個讀 `PPT` 的地方，
    只剩本測試在比對兩者。而它的 `hero_frame_in`／`wide_frame_in` 是**已移除的
    `build_ppt`** 的圖框，與 deck skill 的幾何（CW 12.333in、圖區高 4.68in）
    完全是兩套數字。

    ⚠ 留著的風險是**假知識**：日後有人改 `PPT` profile 以為會影響簡報，其實不會，
    而且不會有任何東西報錯。故刪除，並以本測試守住「只剩一份」。
    """

    def test_ppt_profile_is_gone(self) -> None:
        self.assertFalse(hasattr(chart_sizing, "PPT"),
                         "PPT profile 已無消費者，不得留著當假知識")

    def test_web_keeps_its_values(self) -> None:
        """刪 PPT 不得順手動到 WEB 的值——本次只清死程式碼，不改產圖尺寸。"""
        web = chart_sizing.WEB
        self.assertEqual(web.canvas_width, 1180)
        self.assertEqual(web.canvas_max_height, 560)
        self.assertEqual(web.data_target_pt, 11.25)   # 15px @96dpi
        self.assertEqual(web.note_target_pt, 11.25)   # 使用者「都維持在 15」
        self.assertEqual(web.row_height, 32)

    def test_ppt_only_fields_removed(self) -> None:
        """只有 PPT profile 在用的圖框欄位一併移除（欄位留著也是假知識）。"""
        for field in ("hero_frame_in", "wide_frame_in", "wide_aspect_min"):
            with self.subTest(field=field):
                self.assertFalse(hasattr(chart_sizing.WEB, field))


if __name__ == "__main__":
    unittest.main()
