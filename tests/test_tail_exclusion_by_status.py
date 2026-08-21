"""截止效應改由**法律狀態**推導，不再固定排除末 N 年（2026-08-19 使用者裁決）。

## 為什麼用狀態不用年份

固定「排除末兩年」在滑雪機與割草機**剛好都對**，但那是巧合——它不知道自己在
排除什麼，只是數了兩年。

未決比（該年可見案件中還在審查／公開的比例）直接量到真相：

    年     滑雪機    割草機
    2023     0%      29%
    2024    50%      35%
    2025   100%      67%
    2026   100%     100%

未決比 100% 代表「這一年看得到的案子一件都還沒有結局」——看到的只是最早公開
的那一小撮，其餘還沒進資料。

⚠ 這同時解掉「相對本批最新年 vs 相對今年」的兩難：**不需要知道今天是哪一年**。
一份 2022 年匯出的舊資料，它的 2021 年未決比自然偏高（那批裡 2021 案確實大多
未結案），會被正確排除；不必靠「相對今年」去猜，也不會讓報表隨時間變動。

## 只排除連續的末端

⚠ 割草機 2018 年未決比 18%、2022–2023 在 28–29%——那不是「那幾年沒跑完」，
是個別案件還在審。截止效應**必然從最新年開始連續**。不加這條的話會挖掉中間
年份，比現在更糟。

## 門檻 0.60 的地位

兩批實測：50% 會多排除滑雪機的 2024；70% 會少排除割草機的 2025；
**只有 60% 在兩批上都重現現行行為**。
⚠ 但判準是「與既有行為相容」，而既有行為本身沒被獨立驗證過——
60% 是兩批夾出來的相容區間，不是被證明的最佳值。宣告必須這樣寫。
"""
from __future__ import annotations

import unittest

from backend.app.reports import cluster_analytics as ca


def _p(year, status):
    return {"application_year": year, "legal_status": status}


PENDING = "審查中"
GRANTED = "授權"
DEAD = "放棄"


class DeriveTailExclusionTests(unittest.TestCase):
    def test_ski_shape_excludes_two_years(self):
        """滑雪機形狀：2025／2026 全未決 → 排除 2 年（＝現行行為）。"""
        patents = ([_p(2023, GRANTED)] * 2 + [_p(2024, PENDING)] * 5
                   + [_p(2024, GRANTED)] * 5
                   + [_p(2025, PENDING)] * 3 + [_p(2026, PENDING)])
        self.assertEqual(ca.derive_tail_exclusion(patents), 2)

    def test_mower_shape_excludes_two_years(self):
        """割草機形狀：2025 未決 67%、2026 未決 100% → 同樣排除 2 年。

        ⚠ 兩批形狀不同（一個跳、一個漸進）卻得到同一答案，
        正是「門檻取 60%」被兩批夾出來的證據。
        """
        patents = ([_p(2024, PENDING)] * 8 + [_p(2024, GRANTED)] * 15
                   + [_p(2025, PENDING)] * 8 + [_p(2025, GRANTED)] * 4
                   + [_p(2026, PENDING)])
        self.assertEqual(ca.derive_tail_exclusion(patents), 2)

    def test_only_contiguous_tail_is_excluded(self):
        """⚠ 中間年份未決比高不得被挖掉——截止效應必然從最新年連續往回。"""
        patents = ([_p(2018, PENDING)] * 9 + [_p(2018, GRANTED)]   # 中間 90% 未決
                   + [_p(2019, GRANTED)] * 5
                   + [_p(2020, GRANTED)] * 5)                       # 末年已結案
        self.assertEqual(ca.derive_tail_exclusion(patents), 0,
                         "末年已結案就不該排除，中間那年更不該被挖掉")

    def test_all_resolved_excludes_nothing(self):
        """一份完全跑完的舊資料：一年都不砍。這正是固定末 N 年做錯的情境。"""
        patents = [_p(y, GRANTED) for y in range(2010, 2021)]
        self.assertEqual(ca.derive_tail_exclusion(patents), 0)

    def test_all_pending_does_not_exclude_everything(self):
        """⚠ 全部未決時不得把整批排光——那會讓兩個窗都空，
        每個主題回「未分類」，正是我們在修的那種靜默失效。"""
        patents = [_p(y, PENDING) for y in range(2022, 2027)]
        n = ca.derive_tail_exclusion(patents)
        self.assertLess(n, 5, "不得排除所有年份")

    def test_missing_status_falls_back(self):
        """狀態全缺時無從判斷 → 退回常數，不是當成「全部已結案」。

        ⚠ 當成已結案＝一年都不排除，末兩年的假低值會把每個主題拉成衰退。
        """
        patents = [_p(y, None) for y in range(2015, 2027)]
        self.assertEqual(ca.derive_tail_exclusion(patents),
                         ca.STATUS_TAIL_EXCLUDED_YEARS)

    def test_empty_falls_back(self):
        self.assertEqual(ca.derive_tail_exclusion([]),
                         ca.STATUS_TAIL_EXCLUDED_YEARS)
        self.assertEqual(ca.derive_tail_exclusion(None),
                         ca.STATUS_TAIL_EXCLUDED_YEARS)

    def test_threshold_is_named(self):
        """門檻要具名才能被宣告與追問。"""
        self.assertTrue(hasattr(ca, "STATUS_TAIL_PENDING_RATIO"))
        self.assertAlmostEqual(ca.STATUS_TAIL_PENDING_RATIO, 0.60, places=2)


class WindowsUseDerivedTailTests(unittest.TestCase):
    def test_windows_accept_injected_tail(self):
        """窗推導要能吃推導出來的排除年數，否則推導出來也用不上。"""
        years = list(range(2011, 2027))
        a = ca.derive_status_windows(years, tail_excluded=2)
        b = ca.derive_status_windows(years, tail_excluded=0)
        self.assertNotEqual(a, b, "排除年數不同，窗就該不同")
        self.assertEqual(a[1][1], 2024)
        self.assertEqual(b[1][1], 2026)

    def test_default_keeps_constant(self):
        """不傳時退回常數——既有呼叫端零修改。"""
        years = list(range(2011, 2027))
        self.assertEqual(ca.derive_status_windows(years),
                         ca.derive_status_windows(
                             years, tail_excluded=ca.STATUS_TAIL_EXCLUDED_YEARS))


if __name__ == "__main__":
    unittest.main()
