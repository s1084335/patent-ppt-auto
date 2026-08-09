"""使用者選定的候選必須被 finalize 沿用（2026-08-09）。

## 為什麼這是正確性問題而不只是效能

候選去重後，同一批資料會有多個方案（3 群／5 群／7 群…）。使用者選了「3 群」
那個，finalize 就必須用該候選的 λ。

⚠ 現行 `_finalize_with_dpmeans` 是**重新呼叫 `select_lambda`**——它會自己挑
分數最高的那個（可能是 5 群）。結果是：使用者選了 3 群，系統給他 5 群，
而且**不會有任何錯誤訊息**。

順帶解掉浪費：calibrate 已經掃過一次，finalize 再掃一次是白花 6–46 秒，
而且那是使用者按下「確認」之後在等的時間。
"""
from __future__ import annotations

import unittest


class CandidateLambdaResolutionTests(unittest.TestCase):
    def test_uses_lambda_from_selected_candidate(self):
        from backend.app.clustering import runner

        candidate = {"candidate_k": 3, "parameters": {"lambda": 0.87, "lambda_method": "sweep:x"}}
        self.assertEqual(runner._lambda_from_candidate(candidate).value, 0.87)

    def test_keeps_method_for_traceability(self):
        """CLU-008：推導方法要跟著走，否則 run metadata 只剩一個裸數字。"""
        from backend.app.clustering import runner

        candidate = {"parameters": {"lambda": 0.87, "lambda_method": "sweep:x:converged",
                                    "lambda_version": "v2", "lambda_sample_size": 44}}
        result = runner._lambda_from_candidate(candidate)
        self.assertEqual(result.method, "sweep:x:converged")
        self.assertEqual(result.version, "v2")
        self.assertEqual(result.sample_size, 44)

    def test_missing_lambda_returns_none_so_caller_can_recompute(self):
        """⚠ 舊候選（calibrate 時還沒存 λ）要能退回重算，不得 raise。"""
        from backend.app.clustering import runner

        self.assertIsNone(runner._lambda_from_candidate({"parameters": {}}))
        self.assertIsNone(runner._lambda_from_candidate({}))

    def test_finalize_prefers_candidate_lambda_over_rescan(self):
        """⚠ finalize 必須**先**讀候選的 λ，只有讀不到才重掃。

        原本斷言「不得出現 select_lambda」，但那條路徑是必要的：calibrate 時
        還沒存 λ 的舊候選要能繼續 finalize。改為驗「重掃被關在
        `if lambda_result is None` 之後」——那才是真正的契約。
        """
        import inspect

        from backend.app.clustering import runner

        source = inspect.getsource(runner._finalize_with_dpmeans)
        self.assertIn("_lambda_from_candidate", source)
        guard = source.index("if lambda_result is None")
        rescan = source.index("select_lambda(")
        self.assertLess(guard, rescan, "重掃必須在「候選沒有 λ」的守衛之後")


class MultiCandidateCalibrationTests(unittest.TestCase):
    """calibrate 要寫出多個候選（每種群數一個）。"""

    def test_calibration_emits_one_candidate_per_topic_count(self):
        """去重發生在 engine 層（runner 只負責落庫），所以檢查 engine。"""
        import inspect

        from backend.app.clustering import engine

        source = inspect.getsource(engine.plan_dpmeans_calibration)
        self.assertIn("build_candidates", source)

    def test_each_candidate_carries_its_own_lambda(self):
        """⚠ 每個候選要帶自己的 λ——共用一個的話選哪個都一樣。"""
        import math

        from backend.app.clustering import engine

        def unit(*xs):
            n = math.sqrt(sum(x * x for x in xs)) or 1.0
            return [x / n for x in xs]

        vectors, documents = [], []
        for g in range(5):
            for m in range(8):
                v = [0.0] * 5
                v[g] = 1.0
                v[(g + 1) % 5] = 0.03 * (m + 1)
                vectors.append(unit(*v))
                documents.append(f"topic{g} term{g}a term{g}b doc{m}")
        profiles = engine.plan_dpmeans_calibration(
            vectors, documents=documents, elapsed_seconds=0.0)
        lambdas = [p["parameters"]["lambda"] for p in profiles]
        self.assertEqual(len(lambdas), len(set(lambdas)),
                         "每個候選的 λ 必須不同，否則選哪個都一樣")
        self.assertEqual(sum(1 for p in profiles if p["is_recommended"]), 1,
                         "只能有一個被標為推薦")


if __name__ == "__main__":
    unittest.main()
