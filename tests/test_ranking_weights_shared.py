"""候選排序的加權是**唯一定義處**（tasks 3.2）。

兩條路徑要用同一套權重：舊引擎的 k 候選排序（`attach_ranking_scores`）與
DP-Means 的 lambda 掃描排序（`engine.select_lambda`）。

⚠ 複製第二份會讓兩個引擎的品質判準各自漂移，症狀是「同一批資料，兩個引擎挑
出的方案品質標準不一樣」——而且不會有任何錯誤訊息。本檔把「只有一個定義處」
這件事測出來。
"""
from __future__ import annotations

import unittest

from backend.app.clustering.model import (
    RANKING_LOWER_IS_BETTER,
    RANKING_WEIGHTS,
    attach_ranking_scores,
    rank_candidates,
)


class WeightDefinitionTests(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(RANKING_WEIGHTS.values()), 1.0, places=9)

    def test_coherence_is_heaviest(self):
        """主題內部是否在講同一件事，是最重要的一項。"""
        self.assertEqual(max(RANKING_WEIGHTS, key=RANKING_WEIGHTS.get), "coherence")

    def test_small_topic_ratio_is_lower_better(self):
        """⚠ 這一項越小越好，正規化時要反向——搞反會讓最碎的方案勝出。"""
        self.assertIn("small_topic_ratio", RANKING_LOWER_IS_BETTER)


class RankCandidatesTests(unittest.TestCase):
    GOOD = {"coherence": 0.8, "diversity": 0.9, "balance": 0.9, "small_topic_ratio": 0.1}
    BAD = {"coherence": 0.3, "diversity": 0.4, "balance": 0.5, "small_topic_ratio": 0.8}

    def test_better_metrics_score_higher(self):
        scores = rank_candidates([self.GOOD, self.BAD])
        self.assertGreater(scores[0], scores[1])

    def test_empty_input_returns_empty(self):
        self.assertEqual(rank_candidates([]), [])

    def test_none_metric_counts_as_worst_not_skipped(self):
        """⚠ 指標算不出來的候選不得因為「少扣分」而勝出。

        跳過 None 會讓一個 coherence 算不出來的候選在該項不被扣分，等於白送
        分數——那正好獎勵了品質最不明的方案。
        """
        missing = dict(self.GOOD, coherence=None)
        scores = rank_candidates([self.GOOD, missing])
        self.assertGreater(scores[0], scores[1])

    def test_identical_candidates_tie(self):
        scores = rank_candidates([self.GOOD, dict(self.GOOD)])
        self.assertEqual(scores[0], scores[1])

    def test_lower_is_better_column_is_inverted(self):
        """small_topic_ratio 較小者應得較高分（其餘指標相同）。"""
        few_small = dict(self.GOOD, small_topic_ratio=0.0)
        many_small = dict(self.GOOD, small_topic_ratio=0.9)
        scores = rank_candidates([few_small, many_small])
        self.assertGreater(scores[0], scores[1])


class AttachRankingScoresTests(unittest.TestCase):
    """舊引擎的入口沿用同一套加權——不得有第二份實作。"""

    def test_empty_results_return_empty(self):
        self.assertEqual(attach_ranking_scores([]), [])

    def test_scores_match_rank_candidates(self):
        """⚠ 這是「只有一個定義處」的實際驗證：兩個入口必須算出同一組分數。"""
        from backend.app.clustering.model import TopicModelRunResult

        def _result(metrics):
            return TopicModelRunResult(
                scheme_name="s", topic_model=None, topics=[0, 1], assignments=[],
                topic_info=[], representative_docs={}, representative_doc_indices={},
                metrics=metrics)

        metrics = [
            {"coherence": 0.8, "diversity": 0.9, "balance": 0.9, "small_topic_ratio": 0.1},
            {"coherence": 0.4, "diversity": 0.5, "balance": 0.6, "small_topic_ratio": 0.7},
        ]
        attached = attach_ranking_scores([_result(m) for m in metrics])
        expected = rank_candidates(metrics)
        for result, score in zip(attached, expected):
            self.assertAlmostEqual(result.score, score, places=9)


if __name__ == "__main__":
    unittest.main()
