"""技術狀態的時間窗必須由本批資料推導，不得是絕對年份（2026-08-19）。

## 病徵

`STATUS_EARLY_YEARS = (2011, 2019)`／`STATUS_RECENT_YEARS = (2020, 2024)` 是
**絕對年份**，切自滑雪機那批資料的年度分布。

換一批 2000–2010 的資料會怎樣：兩個窗都落在資料之外 → `recent = early = 0`
→ `in_window = 0` → `ratio = 0.0` → `classify_topic_status` 的四個比較全部是
`0 < 0`＝假 → **每個主題都回「未分類」**。

⚠ 失效方式是**靜默**的：不報錯、不警告，報表就是一片「未分類」，
讀者會以為這批資料本來就分不出狀態。這是缺席型偏差裡最貴的一種。

## 判準

1. 推導出的窗，套在**滑雪機那批年份**上必須**與現行常數逐字相同**
   ——否則這次改動就順手改了現有報表的判定結果，那是兩件事混在一起
2. 換一批年份（例如 2000–2010），窗要落在**那批資料裡**
3. 末兩年一律排除（資料截止效應：新案還在審查中未公開，
   併進近期窗會把每個主題都拉成「衰退」）
4. 跨度不足以切窗時要**回得出「不能切」**，不能回一組落在資料外的窗
"""
from __future__ import annotations

import unittest

from backend.app.reports import cluster_analytics as ca


class DeriveStatusWindowsTests(unittest.TestCase):
    # 滑雪機那批的申請年（實測 report_trial_20260819_143341 的 annual_trend）
    SKI_YEARS = [2011, 2013, 2015, 2016, 2017, 2019, 2020, 2022,
                 2023, 2024, 2025, 2026]

    def test_reproduces_current_constants_on_the_original_batch(self):
        """⚠ 這條是本次改動的安全帶：推導結果必須等於現行寫死的常數。

        不相等就代表我在「移除資料綁定」的同時**順手改了判定結果**——
        那會讓現有報表的技術狀態悄悄變動，而那是另一個決定。
        """
        early, recent = ca.derive_status_windows(self.SKI_YEARS)
        self.assertEqual(early, (2011, 2019), "早期窗與現行常數不符")
        self.assertEqual(recent, (2020, 2024), "近期窗與現行常數不符")

    def test_windows_land_inside_another_batch(self):
        """換一批年份，窗要落在那批資料裡——這正是絕對年份做不到的。"""
        years = list(range(2000, 2011))          # 2000–2010
        early, recent = ca.derive_status_windows(years)
        self.assertGreaterEqual(early[0], 2000)
        self.assertLessEqual(recent[1], 2010)
        self.assertLess(early[1], recent[0], "早期窗要在近期窗之前且不重疊")

    def test_tail_years_always_excluded(self):
        """末兩年排除（資料截止效應），不論哪一批。"""
        years = list(range(2000, 2021))          # 2000–2020
        _, recent = ca.derive_status_windows(years)
        self.assertLessEqual(recent[1], 2018,
                             "末兩年（2019、2020）不該進近期窗")

    def test_recent_window_length_is_stable(self):
        """近期窗長度固定，不隨資料量浮動——否則兩批報表的「近期」不是同一回事。"""
        for start in (2000, 2005, 2011):
            years = list(range(start, start + 16))
            _, recent = ca.derive_status_windows(years)
            self.assertEqual(recent[1] - recent[0] + 1,
                             ca.STATUS_RECENT_WINDOW_YEARS,
                             f"起點 {start} 的近期窗長度不對")

    def test_too_short_span_says_so(self):
        """跨度不足以切窗時要回得出「不能切」，不得回一組落在資料外的窗。

        ⚠ 回 None 之後由呼叫端決定怎麼揭露；沉默地回一組假窗會讓所有主題
        變成「未分類」而沒有人知道為什麼。
        """
        self.assertIsNone(ca.derive_status_windows([2024, 2025, 2026]))
        self.assertIsNone(ca.derive_status_windows([]))
        self.assertIsNone(ca.derive_status_windows(None))

    def test_ignores_unusable_year_values(self):
        """None／非數字混進來不得炸，也不得被當成年份。"""
        years = self.SKI_YEARS + [None, "", "n/a"]
        early, recent = ca.derive_status_windows(years)
        self.assertEqual((early, recent), ((2011, 2019), (2020, 2024)))


class ConstantsStillDeclaredTests(unittest.TestCase):
    def test_window_constants_remain_for_basis_declaration(self):
        """⚠ 常數不刪：`threshold_basis` 對它們做雙向對帳，刪了會變成
        「宣告了但常數不存在」。它們降為**推導失敗時的退路**與基準宣告的對象。"""
        self.assertTrue(hasattr(ca, "STATUS_EARLY_YEARS"))
        self.assertTrue(hasattr(ca, "STATUS_RECENT_YEARS"))

    def test_derivation_parameters_are_named(self):
        """推導參數要具名——寫在算式裡就沒人知道它代表什麼，也改不動。"""
        self.assertTrue(hasattr(ca, "STATUS_RECENT_WINDOW_YEARS"))
        self.assertTrue(hasattr(ca, "STATUS_TAIL_EXCLUDED_YEARS"))


if __name__ == "__main__":
    unittest.main()
