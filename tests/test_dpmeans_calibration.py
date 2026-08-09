"""DP-Means 的校準不掃 k（tasks 2.5 Red：隔離 K 選擇路徑）。

## 為什麼一定要隔離

`calibrate_top_level` 會掃七組 k、每組跑一次完整 BERTopic，再挑三個候選給使用者選。
那整條路徑的存在理由是「k 要由人決定」——DP-Means 的主題數由資料與 lambda 決定，
選 k 這件事在它身上**沒有意義**。

⚠ 不隔離的後果不只是浪費：使用者會被要求在三個「保守／平衡／細分」之間選一個
**完全不影響結果**的候選。介面看起來一切正常，選了也沒反應——這種「操作沒有效果」
比報錯更難察覺。

## 品質指標為什麼是 None 而不是 0

coherence／diversity／balance 都算在 c-TF-IDF 的 top terms 上，DP-Means 沒有。
⚠ 填 0.0 會讓前端顯示「這個方案品質 0 分」，那是**憑空捏造的壞消息**；沒有就是
沒有（None），由顯示端決定怎麼標「不適用」。
"""
from __future__ import annotations

import math
import unittest

from backend.app.clustering import dpmeans, engine


def _unit(*xs: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / norm for x in xs]


class DpmeansCalibrationTests(unittest.TestCase):
    VECTORS = [_unit(1.0, 0.0), _unit(0.99, 0.05), _unit(0.98, 0.1),
               _unit(0.0, 1.0), _unit(0.05, 0.99), _unit(-1.0, 0.0)]

    DOCUMENTS = ["ski belt drive", "ski belt motor", "ski drive control",
                 "treadmill deck cushion", "treadmill running damping",
                 "elliptical stride length"]

    def _profiles(self):
        return engine.plan_dpmeans_calibration(
            self.VECTORS, documents=self.DOCUMENTS, elapsed_seconds=1.5)

    def _profile(self):
        """推薦的那個候選。

        ⚠ 2026-08-09 契約變更：`plan_dpmeans_calibration` 由回傳**單一候選**改為
        回傳**每種群數一個候選**的清單。理由有二（使用者定案）：
        ① 只給一個候選時，介面上是「請選擇方案」但實際沒得選，使用者失去調整
           主題數的能力；② `rank_candidates` 是跨候選正規化，候選集合隨掃描密度
           變動會讓分數不穩定——依群數去重後候選集合固定為群數種類。
        本輔助函式取推薦的那個，讓原有斷言的語意不變。
        """
        profiles = self._profiles()
        recommended = [p for p in profiles if p["is_recommended"]]
        return recommended[0] if recommended else profiles[0]

    def test_returns_one_candidate_per_topic_count(self):
        """每種群數一個候選，且只有一個被標為推薦。"""
        profiles = self._profiles()
        counts = [p["topic_count"] for p in profiles]
        self.assertEqual(len(counts), len(set(counts)), "同一群數不得出現兩個候選")
        self.assertEqual(sum(1 for p in profiles if p["is_recommended"]), 1)

    def test_candidate_type_is_dpmeans(self):
        """⚠ 候選由資料產生（每種群數一個），不是固定的保守／平衡／細分三選一。"""
        for profile in self._profiles():
            self.assertEqual(profile["candidate_type"], "dpmeans")

    def test_topic_count_comes_from_data(self):
        profile = self._profile()
        expected = dpmeans.fit(
            self.VECTORS, lambda_=dpmeans.derive_lambda(self.VECTORS).value)
        self.assertEqual(profile["topic_count"], len(expected.centers))

    def test_k_mirrors_topic_count_not_a_choice(self):
        """k 欄位沿用既有 schema，值＝實際群數。⚠ 它不是使用者選的，只是相容欄位。"""
        profile = self._profile()
        self.assertEqual(profile["k"], profile["topic_count"])

    def test_quality_metrics_are_computed(self):
        """coherence／diversity 照常算——使用者要能拿它跟舊引擎的候選比較。"""
        profile = self._profile()
        self.assertIsNotNone(profile["diversity"])
        self.assertGreater(profile["diversity"], 0.0)

    def test_balance_is_computed_from_sizes(self):
        """balance 只看各群件數分布，本來就與 c-TF-IDF 無關。"""
        profile = self._profile()
        self.assertIsNotNone(profile["balance"])

    def test_small_topic_ratio_is_computed(self):
        """⚠ 這個指標**算得出來**（單件主題佔比），而且正是 lambda 太小的警訊。"""
        profile = self._profile()
        self.assertIsInstance(profile["small_topic_ratio"], float)
        self.assertGreaterEqual(profile["small_topic_ratio"], 0.0)
        self.assertLessEqual(profile["small_topic_ratio"], 1.0)

    def test_lambda_and_method_recorded(self):
        """CLU-008：lambda 的值與推導方法都要留下。"""
        profile = self._profile()
        self.assertGreater(profile["parameters"]["lambda"], 0.0)
        self.assertTrue(profile["parameters"]["lambda_method"])
        self.assertTrue(profile["parameters"]["lambda_version"])

    def test_elapsed_recorded(self):
        self.assertEqual(self._profile()["elapsed_seconds"], 1.5)

    def test_reproducible(self):
        """同一批資料兩次校準要得到同一組候選（含 lambda）。"""
        self.assertEqual(self._profiles(), self._profiles())

    def test_single_cluster_data_still_yields_candidate(self):
        """⚠ 全部擠成一群也要能產候選——那是有效結果，不是錯誤。"""
        tight = [_unit(1.0, 0.0), _unit(0.999, 0.01), _unit(0.998, 0.02)]
        profiles = engine.plan_dpmeans_calibration(
            tight, documents=["a b", "a c", "a d"], elapsed_seconds=0.1)
        self.assertTrue(profiles, "全軍覆沒也要產出候選，讓使用者看到結果再判斷")
        self.assertGreaterEqual(profiles[0]["topic_count"], 1)


if __name__ == "__main__":
    unittest.main()
