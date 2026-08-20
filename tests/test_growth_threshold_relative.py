"""「成長得比整體快」的基準必須是**本批的整體**，不是寫死的數字（2026-08-19）。

## 為什麼這兩個一定要推導

`STATUS_GROWTH_HIGH` 的原註解自己寫明了語意：

    全庫基準 R＝38/55＝0.69——**高於它才叫成長得比整體快**

「比整體快」是**比較型**判準：它的基準就是「整體」，而整體隨批次改變。
寫成常數等於拿甲批的整體去衡量乙批的主題。

⚠ 更糟的是實測發現這個常數連**它自己宣稱的依據**都對不上：
38/55 的 55 是 **workspace 成員數**，但判定實際跑在 **44 件的分群母體**上，
真正的 R＝30/40＝**0.75**。所以 0.70 既不是別批的尺，也不是本批的尺。

## 判準

1. 基準 R＝近期窗件數 ÷（近期窗＋早期窗件數），母體＝**傳進來的那批**
2. 停滯帶以 R 為中心、半寬取自具名常數（半寬本身仍是選擇，要能被宣告）
3. ⚠ 空母體不得炸，也不得回一個會讓所有主題都成立的值
"""
from __future__ import annotations

import unittest

from backend.app.reports import cluster_analytics as ca


class DeriveGrowthBaselineTests(unittest.TestCase):
    def test_baseline_is_recent_share_of_the_batch(self):
        """R＝近期 ÷（近期＋早期）。滑雪機分群母體實測 30/40＝0.75。"""
        self.assertAlmostEqual(ca.derive_growth_baseline(30, 10), 0.75, places=4)

    def test_baseline_follows_the_batch(self):
        """換一批就換一個基準——這正是寫死做不到的。"""
        self.assertAlmostEqual(ca.derive_growth_baseline(10, 30), 0.25, places=4)
        self.assertAlmostEqual(ca.derive_growth_baseline(20, 20), 0.50, places=4)

    def test_empty_window_is_safe(self):
        """⚠ 兩窗都空時不得炸，也不得回 0——回 0 會讓每個主題的
        `ratio >= baseline` 都成立，全部誤判成新興。"""
        r = ca.derive_growth_baseline(0, 0)
        self.assertIsNone(r, "空母體應回 None 由呼叫端決定，不得給一個假基準")

    def test_stagnant_band_centres_on_the_baseline(self):
        """停滯帶以基準為中心；半寬取自具名常數。"""
        band = ca.derive_stagnant_band(0.75)
        self.assertAlmostEqual((band[0] + band[1]) / 2, 0.75, places=4)
        self.assertAlmostEqual((band[1] - band[0]) / 2,
                               ca.STATUS_STAGNANT_HALF_WIDTH, places=4)

    def test_band_stays_inside_zero_one(self):
        """⚠ 基準接近 0 或 1 時帶會溢出比例的定義域——夾限，不得出現負數或 >1。"""
        low = ca.derive_stagnant_band(0.03)
        high = ca.derive_stagnant_band(0.98)
        self.assertGreaterEqual(low[0], 0.0)
        self.assertLessEqual(high[1], 1.0)

    def test_half_width_is_named(self):
        """半寬是**選擇**不是推導，必須具名才能被宣告與追問。"""
        self.assertTrue(hasattr(ca, "STATUS_STAGNANT_HALF_WIDTH"))


class ReproducesCurrentVerdictsTests(unittest.TestCase):
    """安全帶：改成推導後，滑雪機那批的判定結果不得改變。

    ⚠ 實測（`measure_derived_thresholds`）：推導值 0.75／(0.65, 0.85) 與原本
    0.70／(0.59, 0.79) 不同，但 13 個主題狀態**變動 0 個**。
    本測試把那個結論固定下來——用一組會落在兩種門檻之間的比例來驗，
    確認差異不影響分類走向。
    """

    def _metrics(self, **kw):
        base = {"patent_count": 10, "recent_count": 7, "early_count": 3,
                "recent_applicants": 4, "early_applicants": 2,
                "share_recent": 0.3, "share_early": 0.1,
                "concentration_recent": 0.5, "concentration_early": 0.4}
        base.update(kw)
        return base

    def test_growing_wins_before_thresholds_apply(self):
        """⚠ 優先序在門檻之前：件數與家數同步上升就判成長，
        不會走到 GROWTH_HIGH／STAGNANT_BAND——這是變動 0 個的主因。"""
        s = ca.classify_topic_status(self._metrics(), median_count=7)
        self.assertEqual(s, ca.TOPIC_STATUS_GROWING)

    def test_thresholds_accept_injected_baseline(self):
        """門檻要能由呼叫端傳入推導值，否則推導出來也用不上。"""
        m = self._metrics(recent_count=7, early_count=3,
                          recent_applicants=2, early_applicants=2)
        # ratio = 0.70：落在舊帶 (0.59,0.79) 內、落在新帶 (0.65,0.85) 內
        s = ca.classify_topic_status(m, median_count=7,
                                     growth_high=0.75,
                                     stagnant_band=(0.65, 0.85))
        self.assertEqual(s, ca.TOPIC_STATUS_MATURE)

    def test_defaults_keep_old_behaviour(self):
        """不傳門檻時退回常數——既有呼叫端零修改。"""
        m = self._metrics(recent_count=7, early_count=3,
                          recent_applicants=2, early_applicants=2)
        self.assertEqual(ca.classify_topic_status(m, median_count=7),
                         ca.TOPIC_STATUS_MATURE)


if __name__ == "__main__":
    unittest.main()
