"""批 3：圖表列標籤不得被畫布左緣裁掉（H-3，2026-08-03 第三輪實機）。

## 現象

p8 的 `A63B-021`／`A63B-022`、p10 的 `A63B-0021`／`A63B-0022` 開頭那個字被切掉，
畫面上看到的是 `3B-021`。同一張圖裡較短的標籤（`A63B-069`）沒事。

## 根因

`label_gutter` 依 `_display_width` 估最長標籤，實測 SVG 是 `x=271, text-anchor="end"`
——最長標籤估 259px，理論上塞得下，**實際渲染卻超過 271px**。
也就是估算本身偏小：英數係數 0.55 低估了實際字型的字寬
（大寫字母約 0.7 em、數字約 0.56 em，混排平均高於 0.55）。

⚠ G-3 那輪只把「寫死 310px」改成「依內容算」，沒有驗**算出來的值準不準**——
估算偏小時，症狀與寫死一模一樣。

## 判準

用實機那五個標籤，文字起點（gutter − padding − 實際寬度）必須留有安全邊界，
不能剛好貼齊 0。
"""
from __future__ import annotations

import unittest

from backend.app.reports import chart_runner as cr

# 實機 p8（IPC 五階）的標籤，含最長的兩個。
LIVE_LABELS = [
    "A63B-069　特殊運動訓練器械",
    "A63B-021　阻力式肌力訓練器械",
    "A63B-022　心肺與協調訓練器械",
    "A63B-023　特定部位訓練器械",
    "F03G-005　人力機械動力裝置",
]


class LabelWidthEstimateTests(unittest.TestCase):
    """字寬估算不得低估——低估的症狀與「寫死太窄」完全一樣。"""

    def test_alnum_coefficient_not_underestimated(self):
        """`A63B-021` 八個字元的實際寬度接近 5 em，不是 4.4 em。"""
        self.assertGreaterEqual(cr._display_width("A63B-021"), 4.9,
                                "英數字寬低估——標籤會超出算好的標籤區")

    def test_cjk_still_full_width(self):
        self.assertAlmostEqual(cr._display_width("訓練器械"), 4.0, places=2)


class LabelGutterTests(unittest.TestCase):
    """標籤區要放得下實機最長標籤，並留安全邊界。"""

    #: 文字左緣至少離畫布邊緣這麼多 px。
    #: ⚠ 不能只留個位數：`_display_width` 是估算，實機誤差量到 12px 以上
    #: （估 259、實際 >271 仍被裁）。留 30 才擋得住字型 fallback 造成的偏差。
    SAFETY_PX = 30

    def test_live_labels_fit_with_margin(self):
        gutter = cr.label_gutter(LIVE_LABELS)
        widest_px = max(cr._display_width(t) for t in LIVE_LABELS) * cr.CHART_LABEL_PX
        # 文字 anchor=end 落在 gutter − LABEL_TEXT_OFFSET_PX（渲染端就是這樣寫的）
        text_x = gutter - cr.LABEL_TEXT_OFFSET_PX
        self.assertGreaterEqual(text_x - widest_px, self.SAFETY_PX,
                                f"標籤起點只剩 {text_x - widest_px:.0f}px，會被畫布裁掉")

    def test_gutter_covers_observed_requirement(self):
        """實機 SVG 量到 x=271 仍不夠——新的 gutter 必須明顯超過它。"""
        self.assertGreater(cr.label_gutter(LIVE_LABELS), 295,
                           "仍不足以容納實機最長標籤（實測 271 會裁）")

    def test_padding_is_named_not_magic(self):
        """兩個數都要有名字——原本是算式裡的 24 與五處字面 `left - 12`。"""
        self.assertTrue(hasattr(cr, "LABEL_GUTTER_PADDING_PX"))
        self.assertTrue(hasattr(cr, "LABEL_TEXT_OFFSET_PX"))


if __name__ == "__main__":
    unittest.main()
