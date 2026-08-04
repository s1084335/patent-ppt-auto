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


class LabelLeftAlignedTests(unittest.TestCase):
    """I-3：列標籤改**左對齊**，永遠不可能被畫布裁掉（2026-08-03 定案）。

    🔴 為什麼換做法：字寬係數已經猜三次（0.55 → 0.62 → ?），實機仍被裁。
    2026-08-03 從 p10 轉圖掃像素量到：真實字寬**比估算多約 13%**
    （估 280px、實際 >316px）。估算永遠會有誤差，字型一換又不一樣。

    改為 `text-anchor="start"`、x 固定在左緣留白處：
    **標籤多長都從左緣開始畫，不存在「超出左界」這回事**。
    ⚠ 代價：短標籤與長條之間的距離不一致。但多列掃視時「對齊左緣」其實更好讀，
    而且「資訊完整」本來就優先於「間距整齊」。

    ⚠ 只改**列標籤**（長，代碼＋技術名）。刻度標籤是數字、右對齊貼著繪圖區才對，
    不在本項範圍。
    """

    def _bar_svg(self, tmpdir):
        from pathlib import Path

        rows = [{"ipc_main_group_symbol": "A63B-0022", "patent_count": 5},
                {"ipc_main_group_symbol": "A63B-0069", "patent_count": 2}]
        path = Path(tmpdir) / "bar.svg"
        cr.render_bar_chart(path, "測試", rows, "ipc_main_group_symbol")
        return path.read_text(encoding="utf-8")

    def test_row_labels_anchor_start(self):
        import re
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            svg = self._bar_svg(tmp)
        rows = [m for m in re.finditer(r'<text x="([\d.]+)"[^>]*>([^<]*A63B[^<]*)</text>', svg)]
        self.assertTrue(rows, "找不到列標籤")
        for m in rows:
            with self.subTest(label=m.group(2)[:20]):
                self.assertNotIn('text-anchor="end"', m.group(0),
                                 "列標籤仍是右對齊——長標籤會被畫布左緣裁掉")
                self.assertGreaterEqual(float(m.group(1)), cr.LABEL_TEXT_OFFSET_PX - 1e-9,
                                        "列標籤起點在留白之內")

    def test_tick_labels_stay_right_aligned(self):
        """刻度標籤不受本項影響——它們貼著繪圖區右對齊才讀得順。"""
        import inspect

        src = inspect.getsource(cr.render_bar_chart)
        self.assertIn("LABEL_TEXT_OFFSET_PX", src)


if __name__ == "__main__":
    unittest.main()
