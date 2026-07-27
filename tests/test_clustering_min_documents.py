"""分群文件數下限與 k 階梯（2026-07-27 使用者定：門檻 50→30，30–49 給更小的 k）。

實機動因：滑雪機 workspace 60 筆專利，但**各通道可用文件數不足 50**——
技術（獨立項）40 筆、功效（效果摘要）49 筆，兩通道都被舊門檻 50 擋下
`ValueError: clustering requires at least 50 documents`。
（60 筆裡 20 筆沒獨立項、11 筆沒效果摘要，正是列表「技術描述 ✓／功效描述 ✓」在標的事。）

## 為何門檻降到 30，但 k 要另外調小
單純把 50 改成 30，30–49 這段會落進原本「<100 → k=(5,10)」的分支——
40 篇分 10 群，每群平均 4 篇，主題零碎到沒有分析價值，
那正是當初設 50 門檻要避免的情況。**放寬門檻不能只讓它「跑得動」。**

故加一段：30–49 掃 **k=(3,5,8)**（使用者定三組）——3 群約 10–16 篇、5 群約 6–10 篇、
8 群約 4–6 篇，讓使用者在「粗／中／細」之間依實際內容挑，而不是只有兩種極端。

## <30 的備案
使用者定案：低於 30 筆改由 **AI 提主題草稿、使用者定案**（沿用既有候選挑選介面），
不走 BERTopic。⚠ 仍維持 `workflows.md` 第 21 行紅線——AI 只給建議，
正式分類由使用者按「採用這組分類」才寫入。此為罕用備案，不為它加重架構。
"""
from __future__ import annotations

import unittest


class MinDocumentsThresholdTests(unittest.TestCase):
    def test_threshold_is_30(self):
        from backend.app.clustering.runner import MIN_CLUSTERING_DOCUMENTS

        self.assertEqual(
            MIN_CLUSTERING_DOCUMENTS, 30,
            "門檻應為 30（實機：滑雪機技術 40／功效 49 筆被舊門檻 50 擋下）")

    def test_below_threshold_raises(self):
        from backend.app.clustering.runner import top_level_k_values

        for n in (0, 1, 29):
            with self.subTest(n=n):
                with self.assertRaises(ValueError):
                    top_level_k_values(n)


class KLadderTests(unittest.TestCase):
    """k 階梯：小資料不測不合理的大主題數。"""

    def test_small_scope_uses_tiny_k(self):
        """30–49 筆：k=(3,5,8)——原本 k=10 在 40 篇下每群才 4 篇，切分無意義。

        給三組（粗／中／細）讓使用者依實際內容挑，不是只有兩種極端。
        """
        from backend.app.clustering.runner import top_level_k_values

        for n in (30, 40, 49):
            with self.subTest(n=n):
                self.assertEqual(top_level_k_values(n), (3, 5, 8))

    def test_medium_scope_unchanged(self):
        """50–99 筆：維持原本的 k=(5,10)，不因這次改動而變。"""
        from backend.app.clustering.runner import top_level_k_values

        for n in (50, 60, 99):
            with self.subTest(n=n):
                self.assertEqual(top_level_k_values(n), (5, 10))

    def test_large_scope_unchanged(self):
        """≥100 筆的既有階梯不得被動到（迴歸保護）。"""
        from backend.app.clustering.runner import top_level_k_values

        self.assertEqual(top_level_k_values(100), (10, 15))
        self.assertEqual(top_level_k_values(198), (10, 15))
        self.assertEqual(top_level_k_values(250), (10, 15, 20))
        self.assertEqual(top_level_k_values(1000), (10, 15, 20, 25, 30, 35, 40))

    def test_every_group_has_meaningful_size(self):
        """任一 k 之下，每群平均文件數都要 ≥ 3.5——否則主題零碎到沒有分析價值。

        下限取 3.5 而非 5：30 筆分 8 群＝每群 3.75 篇，是使用者明確要的最細那一組
        （小資料想看細分時的選項），但再細就沒有意義了。
        """
        from backend.app.clustering.runner import top_level_k_values

        for n in (30, 40, 49, 50, 60, 99, 100, 150, 200, 500):
            for k in top_level_k_values(n):
                with self.subTest(n=n, k=k):
                    self.assertGreaterEqual(
                        n / k, 3.5,
                        f"{n} 篇分 {k} 群＝每群 {n/k:.1f} 篇，切太碎")


if __name__ == "__main__":
    unittest.main()


class InsufficientDocumentsMessageTests(unittest.TestCase):
    """文件數不足的錯誤訊息要說得清楚（2026-07-27 使用者實機看不懂而改）。

    原訊息只有「clustering requires at least N documents」——使用者無從得知是
    「專利本來就少」還是「專利夠但該欄位多半是空的」。實機正是後者：60 筆專利，
    技術通道只有 40 筆有「獨立項」、功效只有 49 筆有「效果摘要」。
    """

    def _message(self, n: int, source_field: str | None) -> str:
        from backend.app.clustering.runner import top_level_k_values

        with self.assertRaises(ValueError) as ctx:
            top_level_k_values(n, source_field=source_field)
        return str(ctx.exception)

    def test_message_names_channel_count_and_column(self):
        """要指明通道、實際筆數、缺哪個欄位。"""
        msg = self._message(20, "wips_independent_claims")
        self.assertIn("技術", msg, "沒說是哪個通道")
        self.assertIn("20", msg, "沒說實際幾筆")
        self.assertIn("獨立項", msg, "沒說缺哪個欄位——使用者不知道要補什麼")
        self.assertIn("30", msg, "沒說下限是多少")

    def test_effect_channel_message(self):
        msg = self._message(20, "effect_summary")
        self.assertIn("功效", msg)
        self.assertIn("效果 摘要", msg)

    def test_message_without_source_field_still_readable(self):
        """沒帶 source_field 時退回通用訊息，仍要有數字（不得只有英文技術訊息）。"""
        msg = self._message(20, None)
        self.assertIn("20", msg)
        self.assertIn("30", msg)

    def test_unknown_source_field_does_not_crash(self):
        """未知通道時不得讓錯誤訊息自己炸——退回通用訊息即可。"""
        msg = self._message(20, "no_such_channel")
        self.assertIn("20", msg)
