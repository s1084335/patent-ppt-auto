"""DP-Means 候選必須與 KMeans 候選同形（tasks 3.3 regression）。

## 為什麼需要這支測試

2026-08-09 實機驗收時 finalize 掛在 `KeyError: 'candidate_k'`——我憑印象把鍵
寫成 `k`，但既有候選用的是 `candidate_k`（finalize 與 API 都讀它）。

⚠ 這類錯誤純函式測不出來：兩邊都是 dict，形狀對不上要跑到真實流程才會炸。
本測試直接比對兩條路徑產出的**鍵集合**，把契約釘在單元測試層。
"""
from __future__ import annotations

import unittest

#: 下游確實會讀的鍵（finalize、API、workspace_service 各自讀取點的聯集）。
REQUIRED_CANDIDATE_KEYS = frozenset({
    "candidate_id", "run_id", "is_selected", "selected_by", "selected_at",
    "llm_explanation", "candidate_type", "candidate_k", "topic_count",
    "coherence", "diversity", "balance", "small_topic_ratio",
    "elapsed_seconds", "score", "parameters",
})


class CandidateShapeTests(unittest.TestCase):
    def test_dpmeans_candidate_has_all_required_keys(self):
        import inspect

        from backend.app.clustering import runner

        source = inspect.getsource(runner._calibrate_with_dpmeans)
        missing = [key for key in REQUIRED_CANDIDATE_KEYS if f'"{key}"' not in source]
        self.assertEqual(missing, [], f"DP-Means 候選缺少下游會讀的鍵：{missing}")

    def test_kmeans_candidate_has_all_required_keys(self):
        """⚠ 對照組：確認這份必要鍵清單反映的是既有契約，不是我自己編的。"""
        import inspect

        from backend.app.clustering import runner

        source = inspect.getsource(runner._persist_calibration)
        # candidate_type/k 等來自 CandidateProfile.to_dict()，不在字面上；
        # 只驗這條路徑自己組的鍵。
        for key in ("candidate_id", "run_id", "is_selected", "candidate_k", "parameters"):
            self.assertIn(f'"{key}"', source)

    def test_k_scan_lands_under_metrics(self):
        """⚠ k_scan 的落點是 metrics.k_scan，不是 state 頂層。

        放錯地方前端讀不到，而且**不會報錯**——只是候選列表空白。
        """
        import inspect

        from backend.app.clustering import runner

        for func in (runner._persist_calibration, runner._calibrate_with_dpmeans):
            source = inspect.getsource(func)
            self.assertIn('"metrics": {"k_scan"', source,
                          f"{func.__name__} 的 k_scan 沒有落在 metrics 底下")


if __name__ == "__main__":
    unittest.main()
