"""行動判定的門檻一律由本批推導，不得寫死（2026-08-20 使用者裁決）。

## 病徵

使用者：「機會四象限數字都是中位數，所以邏輯用中位數但數字不能寫死」。

實查 `action_space` 的三個數值比較：
- `pending_count >= 1`、`patent_count >= 1` → 「至少一件」的自然邊界，不是門檻
- **`max_share >= 50`** → 寫死的集中度門檻，就掛在象限判斷旁邊

## 為什麼 50 不能留

兩批實測 `max_share` 的中位數：

    滑雪機技術  40      → 50 是「前 20%」（5 個主題只有 1 個過）
    割草機技術  56.5    → 50 是「前 60%」（10 個主題有 6 個過）

**同一個數字，兩批講的不是同一件事。** 而象限的「集中持有」本身是中位數推導的
——於是「集中」在同一支規則裡有兩個定義，那正是本專案反覆在消滅的東西。

⚠ 誠實記錄另一種讀法：50% 有「單一持有人過半」的絕對意義，不是隨手抓的數字。
但實測顯示它在兩批的**相對位置**差很多，而這條規則的用途是排序與比較
（「這個主題的權利集中程度值不值得追」），比較型判準就該用本批的尺。
"""
from __future__ import annotations

import unittest

from backend.app.reports import action_space as A


def _row(**kw):
    base = {"label": "T", "patent_count": 10, "status": "成熟",
            "quadrant": "多方投入技術", "pending_count": 0, "max_share": 40}
    base.update(kw)
    return base


class ThresholdsObjectTests(unittest.TestCase):
    def test_thresholds_carries_both_derived_values(self):
        """門檻物件要同時帶件數中位數與集中度中位數——之後再加推導值
        不必再改 10 個規則的簽章。"""
        t = A.Thresholds(median_count=10, median_max_share=40)
        self.assertEqual(t.median_count, 10)
        self.assertEqual(t.median_max_share, 40)

    def test_derive_from_rows(self):
        """兩個中位數都由**傳進來的那批**算出。"""
        rows = [_row(patent_count=c, max_share=s)
                for c, s in ((3, 20), (7, 36), (10, 40), (11, 43), (50, 83))]
        t = A.derive_thresholds(rows)
        self.assertEqual(t.median_count, 10)
        self.assertEqual(t.median_max_share, 40)

    def test_derive_handles_empty(self):
        """空批不得炸；回 0 讓所有比較都成立是錯的，故用 None 表示無從判斷。"""
        t = A.derive_thresholds([])
        self.assertIsNone(t.median_max_share)


class ConcentrationRuleTests(unittest.TestCase):
    """`確認權利集中程度` 改用本批中位數。"""

    def test_above_batch_median_holds(self):
        t = A.Thresholds(median_count=10, median_max_share=40)
        r = _row(quadrant="多方投入技術", max_share=43)
        self.assertEqual(A.scan_topic(r, t)["確認權利集中程度"], A.HOLDS)

    def test_below_batch_median_fails(self):
        t = A.Thresholds(median_count=10, median_max_share=40)
        r = _row(quadrant="多方投入技術", max_share=36)
        self.assertEqual(A.scan_topic(r, t)["確認權利集中程度"], A.FAILS)

    def test_same_share_flips_with_the_batch(self):
        """⚠ 核心：**同一個 max_share 在不同批次得到不同判定**。

        這正是寫死 50 做不到的——50 在滑雪機技術是前 20%、在割草機技術是前 60%。
        """
        r = _row(quadrant="多方投入技術", max_share=50)
        ski = A.Thresholds(median_count=10, median_max_share=40)      # 50 > 40
        mower = A.Thresholds(median_count=22, median_max_share=56.5)  # 50 < 56.5
        self.assertEqual(A.scan_topic(r, ski)["確認權利集中程度"], A.HOLDS)
        self.assertEqual(A.scan_topic(r, mower)["確認權利集中程度"], A.FAILS)

    def test_quadrant_still_wins(self):
        """象限已判「集中持有」時直接成立，不必再看 share。"""
        t = A.Thresholds(median_count=10, median_max_share=90)
        r = _row(quadrant="集中持有", max_share=10)
        self.assertEqual(A.scan_topic(r, t)["確認權利集中程度"], A.HOLDS)

    def test_no_signal_is_unknown(self):
        """⚠ 兩個訊號都缺 → 證據不足，不是不成立。
        「這份資料不夠」與「判出來不成立」給使用者的下一步不同。"""
        t = A.Thresholds(median_count=10, median_max_share=40)
        r = _row(quadrant=None, max_share=None)
        self.assertEqual(A.scan_topic(r, t)["確認權利集中程度"], A.UNKNOWN)

    def test_missing_batch_median_is_unknown_not_pass(self):
        """⚠ 批次中位數算不出來（空批）時不得放行——沒有尺就量不了。"""
        t = A.Thresholds(median_count=10, median_max_share=None)
        r = _row(quadrant="多方投入技術", max_share=99)
        self.assertEqual(A.scan_topic(r, t)["確認權利集中程度"], A.UNKNOWN)


class NoHardcodedThresholdTests(unittest.TestCase):
    def test_no_literal_fifty_left(self):
        """⚠ 反向鎖：原始碼不得再出現 `>= 50` 這類寫死比較。

        只掃比較運算式，不掃註解——註解要留實測數字當沿革。
        """
        import ast
        import inspect

        src = inspect.getsource(A)
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comp in node.comparators:
                    if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
                        if comp.value not in (0, 1):     # 0/1 是自然邊界
                            bad.append(f"line {comp.lineno}: 比較常數 {comp.value}")
        self.assertEqual(bad, [], f"仍有寫死的門檻比較：{bad}")


class CoverageStillCompleteTests(unittest.TestCase):
    def test_scan_covers_whole_pool(self):
        """介面改了，完整掃描的保證不能掉。"""
        t = A.Thresholds(median_count=10, median_max_share=40)
        self.assertEqual(len(A.scan_topic(_row(), t)), len(A.ACTION_POOL))

    def test_workspace_scan_reports_coverage(self):
        rows = [_row(), _row(patent_count=3)]
        out = A.scan_workspace(rows, A.derive_thresholds(rows))
        self.assertEqual(out["covered"], f"{len(A.ACTION_POOL)}/{len(A.ACTION_POOL)}")


if __name__ == "__main__":
    unittest.main()
