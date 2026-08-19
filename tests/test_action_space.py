"""行動候選池與引擎判定（tasks §9.7）。

## 使用者設計（2026-08-19）

> 資料分析 → 掃描所有可成立的行動方向 → 每個方向判斷「成立／不成立／證據不足」
> → 只輸出成立的專利行動

Verifier 檢查的**不是**「你有沒有寫 ≥4 個」，而是「所有候選方向你是不是都評估了」。
`covered 8/8` 就 PASS，即使最後只有 2 個成立。**行動數量不限，但行動空間必須完整掃描。**

## 兩個裁決塑造了這裡的設計

1. **候選池只放判得出來的**：入池條件＝能寫出一條只用引擎欄位的判定規則。
   B（需「本公司」概念）、D（需主題相鄰度）、G 後半（需替代訊號）不進池，
   明文列在「已知未涵蓋」。⚠ **不得**把它們降級成「證據不足」——
   證據不足＝這份資料不夠（換資料可能就判得出來），那三個是系統沒有這個判準，
   換再多資料都一樣。混在一起會讓使用者一直去補資料而真因無處可見。

2. **引擎算候選 ＋ CLI 可覆寫**：判定規則全是確定性的，引擎算得出來
   （保證不漏掃、零 token、covered 恆等成立）。CLI 可加可否決，但要留痕。
   ⚠ 純引擎判定的盲點是「規則變天花板」——規則沒涵蓋的真實機會永遠不會出現，
   那正是形式鎖的核心機制。所以規則是**地板不是天花板**。
"""
from __future__ import annotations

import unittest

from backend.app.reports import action_space as A


class PoolRegistryTests(unittest.TestCase):
    """候選池本身：每一項都要有判定規則與語意說明。"""

    def test_pool_is_not_empty(self):
        self.assertTrue(A.ACTION_POOL, "候選池是空的")

    def test_every_action_has_rule_and_purpose(self):
        for key, item in A.ACTION_POOL.items():
            with self.subTest(action=key):
                self.assertTrue(callable(item.rule), f"{key} 沒有判定規則")
                self.assertTrue(str(item.purpose).strip(),
                                f"{key} 沒有語意說明——CLI 只能望文生義")
                self.assertTrue(str(item.signal).strip(),
                                f"{key} 沒有寫出它讀哪些引擎欄位")

    def test_known_gaps_are_declared_not_hidden(self):
        """🔴 判不出來的要明文列出，不得偷偷塞進「證據不足」。"""
        self.assertTrue(A.KNOWN_GAPS, "沒有宣告已知未涵蓋")
        for gap in A.KNOWN_GAPS:
            with self.subTest(gap=gap["action"]):
                self.assertTrue(str(gap["missing"]).strip())
                self.assertTrue(str(gap["why_not"]).strip())

    def test_gaps_are_not_in_the_pool(self):
        """⚠ 已知未涵蓋的不得同時出現在池裡——那等於假裝判得出來。"""
        overlap = {g["action"] for g in A.KNOWN_GAPS} & set(A.ACTION_POOL)
        self.assertEqual(overlap, set(), f"未涵蓋項混進池裡：{overlap}")


class ScanIsCompleteTests(unittest.TestCase):
    """🔴 完整掃描：每個主題都要對**每一個**候選方向給判定。"""

    ROW = {"label": "拉繩滑雪", "status": "成長", "patent_count": 10,
           "applicant_count": 9, "max_share": 20, "pending_count": 3,
           "quadrant": "多方投入技術"}

    def test_every_candidate_gets_a_verdict(self):
        verdicts = A.scan_topic(self.ROW, median_count=6)
        self.assertEqual(set(verdicts), set(A.ACTION_POOL),
                         "有候選方向沒被評估——covered 不等於 N/N")

    def test_verdicts_are_from_the_closed_set(self):
        verdicts = A.scan_topic(self.ROW, median_count=6)
        for key, v in verdicts.items():
            with self.subTest(action=key):
                self.assertIn(v, A.VERDICTS, f"{key} 的判定 {v!r} 不在三值內")

    def test_growth_topic_triggers_priority_investment(self):
        """規則要真的會判成立，不是全部回同一個值。"""
        v = A.scan_topic(self.ROW, median_count=6)
        self.assertEqual(v["優先投入"], A.HOLDS)

    def test_declining_topic_triggers_reduce(self):
        row = {**self.ROW, "status": "衰退"}
        self.assertEqual(A.scan_topic(row, median_count=6)["降低投入"], A.HOLDS)
        self.assertEqual(A.scan_topic(row, median_count=6)["優先投入"], A.FAILS)

    def test_insufficient_data_is_distinguished_from_rejection(self):
        """⚠ 「證據不足」與「不成立」不是同一件事。

        缺欄位＝這份資料不夠（換資料可能判得出來）；規則跑完不符＝不成立。
        混在一起，使用者會去補資料補到天荒地老或反過來以為已經否決了。
        """
        row = {"label": "無狀態主題", "patent_count": 8}   # 缺 status
        v = A.scan_topic(row, median_count=6)
        self.assertEqual(v["優先投入"], A.UNKNOWN)

    def test_covered_is_always_full(self):
        """🔴 引擎判定的價值：`covered` 恆等成立，Verifier 不必防偷懶。"""
        report = A.scan_workspace([self.ROW, {**self.ROW, "status": "衰退"}],
                                  median_count=6)
        self.assertEqual(report["covered"],
                         f"{len(A.ACTION_POOL)}/{len(A.ACTION_POOL)}")


class OnlyHoldingActionsAreOutputTests(unittest.TestCase):
    """只輸出成立的行動；數量由資料決定。"""

    def test_output_count_follows_data(self):
        few = A.holding_actions({"label": "冷門", "status": "件數不足",
                                 "patent_count": 3}, median_count=6)
        many = A.holding_actions({"label": "熱門", "status": "成長",
                                  "patent_count": 20, "applicant_count": 9,
                                  "max_share": 80, "pending_count": 5,
                                  "quadrant": "多方投入技術"}, median_count=6)
        self.assertLess(len(few), len(many),
                        "不同資料得到同樣多的行動——規則沒有真的在判")

    def test_single_action_is_allowed(self):
        """⚠ 使用者：「真的只有 1 種成立，也允許只有 1 種。」"""
        v = A.holding_actions({"label": "只有一種", "status": "衰退",
                               "patent_count": 8}, median_count=6)
        self.assertGreaterEqual(len(v), 1)

    def test_no_minimum_count_rule_exists(self):
        """⚠ 不得有「至少 N 項」——那只是把數量鎖換個方向。"""
        import inspect

        from tests.source_assertions import executable_source

        src = executable_source(inspect.getsource(A))
        for bad in ("至少", "最少", "MIN_ACTIONS"):
            with self.subTest(token=bad):
                self.assertNotIn(bad, src, f"出現數量下限「{bad}」")


if __name__ == "__main__":
    unittest.main()
