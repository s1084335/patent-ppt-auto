from __future__ import annotations

import unittest

from backend.app.clustering.runner import (
    ClusteringCorpus,
    KScanResult,
    attach_k_scan_scores,
    calculate_assignment_centroid_distances,
    select_calibration_references,
    select_candidate_profiles,
    top_level_k_values,
)
from backend.app.clustering.model import EmbeddingMatrix


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
        """少於 30 篇不足以支撐主題品質評估，必須先阻擋。

        ⚠ 門檻 2026-07-27 由 50 降為 30（使用者定）：實機滑雪機 60 筆專利，
        但各通道**可用文件數**只有 40／49（不是每筆都有獨立項與效果摘要），
        兩通道都被舊門檻擋下。30–49 這段改掃 k=(3,5,8)，見
        test_clustering_min_documents.py（k 階梯的唯一來源）。
        """
        with self.assertRaises(ValueError):
            top_level_k_values(29)
        # 30–49 不再被擋，且改用更小的 k（40 篇分 10 群每群才 4 篇，切太碎）
        self.assertEqual(top_level_k_values(30), (3, 5, 8))
        self.assertEqual(top_level_k_values(49), (3, 5, 8))

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

    def test_candidate_references_are_deterministic_bounded_and_cover_topics(self) -> None:
        """每個 topic 保存 c-TF-IDF 前 10 筆參照，且排除未分類文件。"""
        patent_ids = list(range(100, 125))
        documents = [f"independent claim {patent_id}" for patent_id in patent_ids]
        topics = [0] * 12 + [1] * 12 + [-1]
        vectors = [[float(index), 0.0] for index in range(len(patent_ids))]
        matrix = EmbeddingMatrix(
            row_numbers=patent_ids,
            patent_numbers=[f"P-{patent_id}" for patent_id in patent_ids],
            vectors=vectors,
        )
        corpus = ClusteringCorpus(
            patent_ids=patent_ids,
            documents=documents,
            matrix=matrix,
            embedding_model="test-model",
            model_version="1",
            preprocessing_version="1",
        )
        representative_doc_indices = {0: list(range(10)), 1: list(range(12, 22))}

        first = select_calibration_references(
            corpus=corpus,
            topics=topics,
            representative_doc_indices=representative_doc_indices,
        )
        second = select_calibration_references(
            corpus=corpus,
            topics=topics,
            representative_doc_indices=representative_doc_indices,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 20)
        self.assertEqual({item["model_topic_id"] for item in first}, {0, 1})
        self.assertNotIn(124, {item["patent_id"] for item in first})
        self.assertEqual(
            [item["rank"] for item in first if item["model_topic_id"] == 0],
            list(range(1, 11)),
        )
        self.assertTrue(all("document" not in item for item in first))
        self.assertTrue(all("keywords" not in item for item in first))
        self.assertTrue(all(item["text_hash"] for item in first))

    def test_k_scan_dict_hides_internal_references_by_default(self) -> None:
        """metrics 與 job result 不應攜帶內部代表文件參照。"""
        result = self._result(k=10)
        result.references = [{"patent_id": 1, "text_hash": "hash"}]
        self.assertNotIn("references", result.to_dict())
        self.assertIn("references", result.to_dict(include_references=True))

    def test_assignment_distances_remain_available_after_ctfidf_selection(self) -> None:
        """finalize 仍須保存 centroid 距離，但不可拿它取代 c-TF-IDF 代表文檔。"""
        distances = calculate_assignment_centroid_distances(
            vectors=[[0.0, 0.0], [2.0, 0.0], [10.0, 0.0], [12.0, 0.0], [99.0, 0.0]],
            topics=[0, 0, 1, 1, -1],
        )

        self.assertEqual(distances[:4], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(distances[4], float("inf"))

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
