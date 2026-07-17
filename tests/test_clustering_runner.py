from __future__ import annotations

import unittest

from backend.app.clustering.runner import (
    KScanResult,
    attach_k_scan_scores,
    select_candidate_profiles,
    top_level_k_values,
)


class TopLevelClusteringRunnerTests(unittest.TestCase):
    """驗證第一層動態主題數與三組候選的固定契約。"""

    def test_top_level_k_range_scales_with_document_count(self) -> None:
        """第一層依資料量逐級增加，600 筆以上才跑完整 10 到 40。"""
        self.assertEqual(top_level_k_values(50), (5, 10))
        self.assertEqual(top_level_k_values(100), (10, 15))
        self.assertEqual(top_level_k_values(200), (10, 15, 20))
        self.assertEqual(top_level_k_values(300), (10, 15, 20, 25))
        self.assertEqual(top_level_k_values(600), (10, 15, 20, 25, 30, 35, 40))

    def test_top_level_k_range_rejects_too_small_workspace(self) -> None:
        """少於 50 篇不足以支撐目前主題品質評估，必須先阻擋。"""
        with self.assertRaises(ValueError):
            top_level_k_values(49)

    def test_candidate_profiles_choose_one_from_each_partition(self) -> None:
        """保守、平衡、細分候選必須各自從指定 k 區間選一組。"""
        scores = {10: 0.1, 15: 0.8, 20: 0.2, 25: 0.9, 30: 0.3, 35: 0.4, 40: 0.7}
        results = [self._result(k=k, score=score) for k, score in scores.items()]

        candidates = select_candidate_profiles(results)

        self.assertEqual(
            [(candidate.candidate_type, candidate.result.k) for candidate in candidates],
            [("conservative", 15), ("balanced", 25), ("detailed", 40)],
        )

    def test_two_k_values_return_two_real_candidates(self) -> None:
        """100 筆級距不複製候選，僅提供保守與細分兩個有效選項。"""
        candidates = select_candidate_profiles(
            [self._result(k=10, score=0.7), self._result(k=15, score=0.8)]
        )

        self.assertEqual(
            [(candidate.candidate_type, candidate.result.k) for candidate in candidates],
            [("conservative", 10), ("detailed", 15)],
        )

    def test_score_is_bounded_and_rewards_better_metrics(self) -> None:
        """排序 score 必須介於 0..1，且較佳的三指標與小群比例應排名較高。"""
        weaker = self._result(k=10, coherence=0.2, diversity=0.3, balance=0.4, small=0.5)
        stronger = self._result(k=15, coherence=0.8, diversity=0.9, balance=0.7, small=0.1)

        attach_k_scan_scores([weaker, stronger])

        self.assertGreater(stronger.score, weaker.score)
        self.assertGreaterEqual(weaker.score, 0.0)
        self.assertLessEqual(stronger.score, 1.0)

    @staticmethod
    def _result(
        *,
        k: int,
        score: float = 0.0,
        coherence: float = 0.5,
        diversity: float = 0.5,
        balance: float = 0.5,
        small: float = 0.2,
    ) -> KScanResult:
        """建立不需載入 BERTopic 的候選測試資料。"""
        return KScanResult(
            k=k,
            topic_count=k,
            coherence=coherence,
            diversity=diversity,
            balance=balance,
            small_topic_ratio=small,
            elapsed_seconds=1.0,
            score=score,
        )


if __name__ == "__main__":
    unittest.main()
