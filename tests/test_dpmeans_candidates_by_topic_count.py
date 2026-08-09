"""通過的掃描點依群數去重成候選（2026-08-09 使用者定案）。

## 兩個問題，同一個解

**問題一：使用者失去調整主題數的能力。** DP-Means 的 calibrate 只產 1 個候選，
但流程仍要求選一個才能 finalize——介面上是「請選擇方案」，實際上沒得選。
舊引擎至少能在保守／平衡／細分之間選。

**問題二：評分對掃描密度敏感。** `rank_candidates` 是**跨候選正規化**，候選集合
一變，所有點的相對分數就變。⚠ 實測：固定 24／36／60 點給技術 7 群，逐步細分到
29 點卻給 6 群——兩種掃法掃到不同位置的點，就選出不同答案。舊引擎裡候選固定
7 個 k 所以沒事，掃描裡候選數隨密度變就不穩定。

**解法**：通過四項判準的點**依群數去重**，每種群數留一個代表。去重後候選集合
＝群數種類（有限且穩定），掃描再密只會讓代表更準，不改變候選集合與相對排序。

## 代表怎麼挑

同一群數的點會形成一段連續的 λ 區間，取**中位 λ**——⚠ 不取分數最高或區間邊緣：
邊緣值換一批資料就掉到隔壁群數去了，中點最耐得住資料微變。
"""
from __future__ import annotations

import unittest

from backend.app.clustering import engine


def _row(lambda_, topic_count, *, failed=(), coherence=0.5, diversity=0.5,
         balance=0.5, small=0.0):
    return {
        "lambda": lambda_, "topic_count": topic_count, "median_size": 5,
        "singleton_doc_share": 0.0, "between_min": 0.8, "coherence": coherence,
        "diversity": diversity, "balance": balance, "small_topic_ratio": small,
        "score": None, "failed": list(failed), "stability_spread": 0,
    }


# 三種群數各佔一段連續區間，外加兩個被刷掉的點
SWEEP = [
    _row(0.50, 12, failed=["median_size"]),
    _row(0.60, 7, coherence=0.80),
    _row(0.62, 7, coherence=0.81),
    _row(0.64, 7, coherence=0.79),
    _row(0.70, 5, coherence=0.60),
    _row(0.72, 5, coherence=0.62),
    _row(0.80, 3, coherence=0.40),
    _row(0.95, 1, failed=["single_cluster"]),
]


class CandidateBuildTests(unittest.TestCase):
    def test_one_candidate_per_distinct_topic_count(self):
        candidates = engine.build_candidates(SWEEP)
        self.assertEqual([c["topic_count"] for c in candidates], [3, 5, 7])

    def test_failed_rows_excluded(self):
        counts = [c["topic_count"] for c in engine.build_candidates(SWEEP)]
        self.assertNotIn(12, counts)
        self.assertNotIn(1, counts)

    def test_representative_is_median_lambda_of_its_run(self):
        """⚠ 取區間中點，不取分數最高或邊緣——邊緣值換批資料就掉到隔壁群數。"""
        candidates = engine.build_candidates(SWEEP)
        seven = [c for c in candidates if c["topic_count"] == 7][0]
        self.assertEqual(seven["lambda"], 0.62)

    def test_candidates_are_scored(self):
        for candidate in engine.build_candidates(SWEEP):
            self.assertIsNotNone(candidate["score"])

    def test_candidate_count_is_stable_under_refinement(self):
        """⚠ 這是整個設計的重點：加密掃描不得改變候選數量。

        在每段區間中插入更多點（模擬細分），候選仍應是同樣的 3 個群數。
        """
        refined = list(SWEEP) + [
            _row(0.61, 7, coherence=0.805), _row(0.63, 7, coherence=0.795),
            _row(0.71, 5, coherence=0.61), _row(0.78, 3, coherence=0.41),
        ]
        before = [c["topic_count"] for c in engine.build_candidates(SWEEP)]
        after = [c["topic_count"] for c in engine.build_candidates(refined)]
        self.assertEqual(before, after)

    def test_empty_or_all_failed(self):
        self.assertEqual(engine.build_candidates([]), [])
        self.assertEqual(
            engine.build_candidates([_row(0.5, 3, failed=["stability"])]), [])

    def test_best_candidate_is_highest_scored(self):
        candidates = engine.build_candidates(SWEEP)
        best = engine.pick_best_candidate(candidates)
        self.assertEqual(best, max(candidates, key=lambda c: (c["score"], c["lambda"])))


if __name__ == "__main__":
    unittest.main()
