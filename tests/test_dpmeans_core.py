"""Cosine Online DP-Means 核心（replace-clustering-with-dpmeans 第 2.1 節 Red）。

## 為什麼要換掉 MiniBatchKMeans

現行增量流程固定 `n_clusters`，`partial_fit` 只會**移動既有中心**——新增資料
即使形成明顯的新群，也只能被硬塞進最近的舊主題，長不出新主題。

## 演算法契約（tasks 1.2；本檔即為契約的可執行版）

- **距離**：cosine distance = 1 − cos(u, v)，向量在進入前已 L2 normalize
- **建群門檻**：與所有既有中心的距離都 > lambda 時建新中心（DP-Means 的核心）
- **中心更新**：online 平均（新點併入後重算質心並重新 normalize）
- **決定性**：同樣輸入 + 同樣順序 → 同樣結果；順序敏感是 DP-Means 的已知性質，
  故對外一律以固定順序餵入，並在此明確測出「換順序可能不同」的界線
- **lambda**：由校準資料推導（見 test_lambda_*），不要求使用者輸入
- **空／小樣本**：0 筆回空結果不 raise；1 筆自成一群

⚠ 本檔只測**純函式核心**，不碰 artifact、DB 與 job——那是 2.3／2.4 的範圍。
"""
from __future__ import annotations

import math
import unittest

from backend.app.clustering import dpmeans


def _unit(*xs: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / norm for x in xs]


class CosineDistanceTests(unittest.TestCase):
    def test_identical_vectors_distance_zero(self):
        v = _unit(1.0, 2.0, 3.0)
        self.assertAlmostEqual(dpmeans.cosine_distance(v, v), 0.0, places=9)

    def test_orthogonal_vectors_distance_one(self):
        self.assertAlmostEqual(
            dpmeans.cosine_distance(_unit(1.0, 0.0), _unit(0.0, 1.0)), 1.0, places=9)

    def test_opposite_vectors_distance_two(self):
        self.assertAlmostEqual(
            dpmeans.cosine_distance(_unit(1.0, 0.0), _unit(-1.0, 0.0)), 2.0, places=9)

    def test_zero_vector_is_max_distance_not_error(self):
        """⚠ 零向量不得炸——嵌入失敗或空文本會產生它，要當成「離所有群都遠」。"""
        self.assertAlmostEqual(dpmeans.cosine_distance([0.0, 0.0], _unit(1.0, 0.0)), 1.0)


class L2NormalizeTests(unittest.TestCase):
    """CLU-009：PCA 後必須重新 L2 normalize，cosine 的前提才成立。"""

    def test_normalizes_to_unit_length(self):
        out = dpmeans.l2_normalize([3.0, 4.0])
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in out)), 1.0, places=9)

    def test_zero_vector_stays_zero(self):
        self.assertEqual(dpmeans.l2_normalize([0.0, 0.0]), [0.0, 0.0])

    def test_already_normalized_unchanged(self):
        v = _unit(1.0, 1.0)
        for a, b in zip(dpmeans.l2_normalize(v), v):
            self.assertAlmostEqual(a, b, places=9)


class ClusterFormationTests(unittest.TestCase):
    """建群門檻：距離超過 lambda 才開新群。"""

    NEAR = [_unit(1.0, 0.0), _unit(0.99, 0.01), _unit(0.98, 0.02)]
    FAR = _unit(0.0, 1.0)

    def test_close_points_share_one_cluster(self):
        result = dpmeans.fit(self.NEAR, lambda_=0.5)
        self.assertEqual(len(result.centers), 1)
        self.assertEqual(result.labels, [0, 0, 0])

    def test_far_point_starts_new_cluster(self):
        result = dpmeans.fit([*self.NEAR, self.FAR], lambda_=0.5)
        self.assertEqual(len(result.centers), 2)
        self.assertEqual(result.labels[-1], 1)

    def test_threshold_is_strict_greater(self):
        """⚠ 邊界值：距離**等於** lambda 時併入既有群，不開新群。

        寫死這一邊是為了讓行為可預測——浮點邊界上兩種都「合理」，但不定死
        就會出現「同一份資料兩次跑出不同群數」。
        """
        a = _unit(1.0, 0.0)
        b = _unit(0.0, 1.0)          # 與 a 的 cosine distance 正好 1.0
        result = dpmeans.fit([a, b], lambda_=1.0)
        self.assertEqual(len(result.centers), 1)

    def test_single_point_forms_one_cluster(self):
        result = dpmeans.fit([_unit(1.0, 0.0)], lambda_=0.5)
        self.assertEqual(len(result.centers), 1)
        self.assertEqual(result.labels, [0])

    def test_empty_input_returns_empty_not_error(self):
        """⚠ 空輸入回空結果、不 raise：workspace 可能還沒有任何文件。"""
        result = dpmeans.fit([], lambda_=0.5)
        self.assertEqual(result.centers, [])
        self.assertEqual(result.labels, [])


class CenterUpdateTests(unittest.TestCase):
    def test_center_moves_toward_members(self):
        a, b = _unit(1.0, 0.0), _unit(1.0, 0.2)
        result = dpmeans.fit([a, b], lambda_=0.5)
        center = result.centers[0]
        self.assertGreater(center[1], 0.0, "中心應被第二點拉離純 x 軸")
        self.assertLess(center[1], b[1], "但不應直接等於第二點")

    def test_center_stays_normalized(self):
        """⚠ 中心也要維持單位長度，否則後續 cosine 距離的尺度會漂掉。"""
        result = dpmeans.fit([_unit(1.0, 0.0), _unit(1.0, 0.3), _unit(1.0, 0.6)],
                             lambda_=0.5)
        for center in result.centers:
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in center)), 1.0, places=9)

    def test_counts_track_membership(self):
        result = dpmeans.fit([_unit(1.0, 0.0), _unit(0.99, 0.01), _unit(0.0, 1.0)],
                             lambda_=0.5)
        self.assertEqual(sorted(result.counts), [1, 2])


class DeterminismTests(unittest.TestCase):
    POINTS = [_unit(1.0, 0.0), _unit(0.9, 0.1), _unit(0.0, 1.0), _unit(0.1, 0.9)]

    def test_same_input_same_output(self):
        first = dpmeans.fit(self.POINTS, lambda_=0.4)
        second = dpmeans.fit(self.POINTS, lambda_=0.4)
        self.assertEqual(first.labels, second.labels)
        self.assertEqual(first.counts, second.counts)

    def test_order_sensitivity_is_bounded(self):
        """⚠ DP-Means **是**順序敏感的演算法——這裡不假裝它不是。

        測的是界線：分得開的資料換順序後群數仍相同（成員可能換編號）。
        對外一律以固定順序餵入，順序本身由呼叫端負責。
        """
        forward = dpmeans.fit(self.POINTS, lambda_=0.4)
        backward = dpmeans.fit(list(reversed(self.POINTS)), lambda_=0.4)
        self.assertEqual(len(forward.centers), len(backward.centers))


class LambdaDerivationTests(unittest.TestCase):
    """CLU-008：lambda 由資料推導、可重現，不要求使用者輸入。"""

    SAMPLE = [_unit(1.0, 0.0), _unit(0.95, 0.05), _unit(0.0, 1.0),
              _unit(0.05, 0.95), _unit(0.7, 0.7)]

    def test_returns_value_and_method(self):
        result = dpmeans.derive_lambda(self.SAMPLE)
        self.assertGreater(result.value, 0.0)
        self.assertTrue(result.method, "推導方法要記錄，不能只有一個數字")
        self.assertTrue(result.version, "版本要記錄，改公式時舊 run 才追得回")

    def test_reproducible(self):
        """相同輸入 → 相同 lambda（CLU-008 明文要求）。"""
        self.assertEqual(dpmeans.derive_lambda(self.SAMPLE).value,
                         dpmeans.derive_lambda(self.SAMPLE).value)

    def test_order_independent(self):
        """⚠ lambda 不得隨餵入順序改變——它是資料的統計量，不是流程的產物。"""
        self.assertAlmostEqual(dpmeans.derive_lambda(self.SAMPLE).value,
                               dpmeans.derive_lambda(list(reversed(self.SAMPLE))).value,
                               places=9)

    def test_tight_sample_gives_smaller_lambda(self):
        """資料越緊密，門檻越小——否則整批會被塞進同一群。"""
        tight = [_unit(1.0, 0.0), _unit(0.99, 0.01), _unit(0.98, 0.02)]
        spread = [_unit(1.0, 0.0), _unit(0.0, 1.0), _unit(-1.0, 0.0)]
        self.assertLess(dpmeans.derive_lambda(tight).value,
                        dpmeans.derive_lambda(spread).value)

    def test_too_few_samples_falls_back_with_reason(self):
        """⚠ 樣本不足時要回退到明確的預設值並說明，不得回 0 或 NaN。"""
        result = dpmeans.derive_lambda([_unit(1.0, 0.0)])
        self.assertGreater(result.value, 0.0)
        self.assertIn("fallback", result.method)

    def test_derives_from_pairwise_not_nearest_neighbor(self):
        """⚠ 2026-08-09 契約修正：門檻由**全體兩兩距離**的低分位推導，
        不是最近鄰距離。

        改的理由是實測推翻了原公式（見 dpmeans.PAIRWISE_QUANTILE 的說明）：
        最近鄰衡量的是「最近的鄰居有多近」，但建群門檻要回答的是「一個群的
        半徑該多大」。在 PatentSBERTa + PCA100 的真實向量上，前者給的門檻讓
        35 份文件碎成 18 群（9 個單點群）。

        本測試釘的是**方向**：門檻必須大於多數點的最近鄰距離，否則幾乎每個點
        都會自成一群。
        """
        # 兩個緊密小群 + 一個離群點：最近鄰距離都很小，但群半徑不小
        sample = [_unit(1.0, 0.0), _unit(0.95, 0.05), _unit(0.9, 0.1),
                  _unit(0.0, 1.0), _unit(0.05, 0.95), _unit(-1.0, 0.2)]
        result = dpmeans.derive_lambda(sample)
        nearest = []
        for i, point in enumerate(sample):
            nearest.append(min(dpmeans.cosine_distance(point, other)
                               for j, other in enumerate(sample) if j != i))
        median_nearest = sorted(nearest)[len(nearest) // 2]
        self.assertGreater(result.value, median_nearest,
                           "門檻小於多數點的最近鄰距離時，幾乎每個點都會自成一群")
        self.assertIn("pairwise", result.method)

    def test_all_identical_documents_falls_back(self):
        """⚠ 全部文件相同時距離全為 0，那不是有效門檻——要回退並說明原因。

        會發生在資料匯入出錯（同一份文件重複匯入）或欄位全空的情況。
        回 0 會讓每個點都自成一群。
        """
        same = [_unit(1.0, 0.0) for _ in range(5)]
        result = dpmeans.derive_lambda(same)
        self.assertGreater(result.value, 0.0)
        self.assertIn("degenerate", result.method)

    def test_large_sample_is_subsampled_deterministically(self):
        """⚠ 全體兩兩距離是 O(n²)：一萬份文件＝五千萬對，會讓校準卡住。

        超過門檻時抽樣，且抽樣必須**可重現**（固定 seed）——否則同一批資料
        兩次校準會得到不同的 lambda，違反 CLU-008 的可重現要求。
        """
        big = [_unit(1.0, i / 500.0) for i in range(dpmeans.PAIRWISE_SAMPLE_LIMIT + 50)]
        first = dpmeans.derive_lambda(big)
        second = dpmeans.derive_lambda(big)
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.sample_size, dpmeans.PAIRWISE_SAMPLE_LIMIT,
                         "sample_size 要據實記錄實際用了幾個點，不是原始筆數")


class IncrementalTests(unittest.TestCase):
    """CLU-004：以既有中心對新文件增量分群，舊 assignment 不動。"""

    BASE = [_unit(1.0, 0.0), _unit(0.99, 0.01)]

    def test_near_point_joins_existing_cluster(self):
        state = dpmeans.fit(self.BASE, lambda_=0.5)
        updated = dpmeans.partial_fit(state, [_unit(0.98, 0.02)], lambda_=0.5)
        self.assertEqual(len(updated.centers), 1)
        self.assertEqual(updated.labels, [0])
        self.assertEqual(updated.new_center_indexes, [])

    def test_far_point_creates_new_topic(self):
        """新文件遠離所有中心 → 建新主題（規格要求並排入 ai:topic_label）。"""
        state = dpmeans.fit(self.BASE, lambda_=0.5)
        updated = dpmeans.partial_fit(state, [_unit(0.0, 1.0)], lambda_=0.5)
        self.assertEqual(len(updated.centers), 2)
        self.assertEqual(updated.new_center_indexes, [1],
                         "要指出哪些是新主題，label job 才知道只跑新的")

    def test_existing_centers_preserved(self):
        """⚠ 增量不得重算既有中心的身分——舊主題的 topic_key 要能對得上。"""
        state = dpmeans.fit(self.BASE, lambda_=0.5)
        before = list(state.centers[0])
        updated = dpmeans.partial_fit(state, [_unit(0.0, 1.0)], lambda_=0.5)
        for a, b in zip(updated.centers[0], before):
            self.assertAlmostEqual(a, b, places=9)

    def test_empty_batch_is_noop(self):
        state = dpmeans.fit(self.BASE, lambda_=0.5)
        updated = dpmeans.partial_fit(state, [], lambda_=0.5)
        self.assertEqual(updated.centers, state.centers)
        self.assertEqual(updated.labels, [])


class ChannelSeparationTests(unittest.TestCase):
    """技術／功效兩通道各自分群，不得互相污染。"""

    def test_two_channels_produce_independent_states(self):
        tech = dpmeans.fit([_unit(1.0, 0.0), _unit(0.99, 0.01)], lambda_=0.5)
        effect = dpmeans.fit([_unit(0.0, 1.0), _unit(0.01, 0.99)], lambda_=0.5)
        self.assertEqual(len(tech.centers), 1)
        self.assertEqual(len(effect.centers), 1)
        self.assertNotAlmostEqual(tech.centers[0][0], effect.centers[0][0], places=3)


if __name__ == "__main__":
    unittest.main()
