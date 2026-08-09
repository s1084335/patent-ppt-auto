"""DP-Means 接上增量流程的分流規則（tasks 2.4 Red）。

## 兩條規則，方向相反

- **新 run（finalize）看 feature flag**：使用者定案「並存，驗收後再切」，
  所以要能用旗標決定這次正式分群用哪個引擎。
- **增量跟隨 artifact 記錄的演算法，不看 flag**：⚠ 中途換引擎會讓中心格式
  對不上——KMeans 的 artifact 沒有 `dpmeans_state`，DP-Means 的沒有 sklearn
  模型。硬換的結果不是報錯，是**分群結果莫名其妙**。

## 為什麼要有這層接縫

`incremental_workspace` 直接呼叫 `partial_fit_bertopic`，混在 DB 交易、
artifact 存檔與 run 狀態之間，完全驗不到。抽出純函式後，分流規則與新主題
識別都能用合成向量決定性地測。
"""
from __future__ import annotations

import unittest

from backend.app.clustering import artifacts, dpmeans, engine


class _FakeKMeansModel:
    """假的 BERTopic/KMeans：只記錄有沒有被呼叫、回固定 topic。"""

    def __init__(self):
        self.calls = 0

    def partial_fit(self, documents, embeddings=None):  # noqa: ARG002
        self.calls += 1
        self.topics_ = [0] * len(documents)


def _kmeans_artifact():
    return artifacts.WorkspaceTopicArtifact(
        workspace_id=3, source_field="wips_independent_claims", run_id=1,
        artifact_version=1, reducer=None, topic_model=_FakeKMeansModel(),
        embedding_model="m", embedding_model_version="v1", preprocessing_version="v1",
    )


def _dpmeans_artifact(lambda_=0.5):
    state = dpmeans.fit([[1.0, 0.0], [0.99, 0.01]], lambda_=lambda_)
    return artifacts.WorkspaceTopicArtifact(
        workspace_id=3, source_field="wips_independent_claims", run_id=1,
        artifact_version=artifacts.ARTIFACT_SCHEMA_VERSION, reducer=None, topic_model=None,
        embedding_model="m", embedding_model_version="v1", preprocessing_version="v1",
        algorithm=artifacts.ALGORITHM_DPMEANS,
        dpmeans_state=artifacts.serialize_dpmeans_state(state, lambda_=lambda_),
    )


class AlgorithmRoutingTests(unittest.TestCase):
    def test_kmeans_artifact_uses_kmeans_path(self):
        artifact = _kmeans_artifact()
        result = engine.predict_incremental(
            artifact, documents=["a", "b"], vectors=[[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(result.algorithm, artifacts.ALGORITHM_KMEANS)
        self.assertEqual(artifact.topic_model.calls, 1)
        self.assertEqual(result.new_topic_indexes, [],
                         "KMeans 固定 k，本來就長不出新主題")

    def test_dpmeans_artifact_uses_dpmeans_path(self):
        artifact = _dpmeans_artifact()
        result = engine.predict_incremental(
            artifact, documents=["a"], vectors=[[0.98, 0.02]])
        self.assertEqual(result.algorithm, artifacts.ALGORITHM_DPMEANS)
        self.assertEqual(result.topics, [0])

    def test_incremental_ignores_feature_flag(self):
        """⚠ 增量**不看** flag：artifact 說是什麼就是什麼。

        中途換引擎不會報錯，只會讓分群結果莫名其妙——那是最難查的一種。
        """
        artifact = _kmeans_artifact()
        result = engine.predict_incremental(
            artifact, documents=["a"], vectors=[[1.0, 0.0]],
            requested_algorithm=artifacts.ALGORITHM_DPMEANS)
        self.assertEqual(result.algorithm, artifacts.ALGORITHM_KMEANS)

    def test_dpmeans_artifact_without_state_fails_loud(self):
        """標成 DP-Means 卻沒有狀態＝artifact 壞了，要當場說，不要默默改走 KMeans。"""
        artifact = _dpmeans_artifact()
        artifact.dpmeans_state = None
        with self.assertRaises(ValueError):
            engine.predict_incremental(artifact, documents=["a"], vectors=[[1.0, 0.0]])


class NewTopicDetectionTests(unittest.TestCase):
    """新主題要被指出來——只有它們需要排 ai:topic_label（CLU-004）。"""

    def test_far_document_creates_new_topic(self):
        artifact = _dpmeans_artifact()
        result = engine.predict_incremental(
            artifact, documents=["far"], vectors=[[0.0, 1.0]])
        self.assertEqual(result.new_topic_indexes, [1])
        self.assertEqual(result.topics, [1])

    def test_near_document_reuses_topic(self):
        artifact = _dpmeans_artifact()
        result = engine.predict_incremental(
            artifact, documents=["near"], vectors=[[0.99, 0.02]])
        self.assertEqual(result.new_topic_indexes, [])

    def test_updated_state_is_returned_for_saving(self):
        """⚠ 增量後的狀態要能存回 artifact，下一批才接得上。"""
        artifact = _dpmeans_artifact()
        result = engine.predict_incremental(
            artifact, documents=["far"], vectors=[[0.0, 1.0]])
        self.assertIsNotNone(result.updated_state)
        self.assertEqual(len(result.updated_state["centers"]), 2)
        # 存回去之後還要能繼續增量
        artifact.dpmeans_state = result.updated_state
        again = engine.predict_incremental(
            artifact, documents=["near-new"], vectors=[[0.01, 0.99]])
        self.assertEqual(again.topics, [1], "應併入剛才建立的新主題")

    def test_empty_batch_is_noop(self):
        artifact = _dpmeans_artifact()
        result = engine.predict_incremental(artifact, documents=[], vectors=[])
        self.assertEqual(result.topics, [])
        self.assertEqual(result.new_topic_indexes, [])


class FinalizeRoutingTests(unittest.TestCase):
    """新 run 才看 feature flag。"""

    def test_default_is_kmeans(self):
        """⚠ 預設必須是舊引擎——使用者要的是「並存，驗收後再切」。"""
        self.assertEqual(engine.resolve_algorithm(None), artifacts.ALGORITHM_KMEANS)

    def test_flag_selects_dpmeans(self):
        self.assertEqual(engine.resolve_algorithm("dpmeans"),
                         artifacts.ALGORITHM_DPMEANS)

    def test_unknown_value_fails_loud(self):
        """打錯字不得靜默退回預設——那會讓人以為切換成功了。"""
        with self.assertRaises(ValueError):
            engine.resolve_algorithm("dp-means")



class EdgeCaseCoverageTests(unittest.TestCase):
    """邊界分支——都是「不常走但走到時不能出錯」的路徑。"""

    def test_kmeans_empty_batch_is_noop(self):
        """⚠ 空批次不得呼叫模型：sklearn 的 partial_fit 收到空清單會炸。"""
        artifact = _kmeans_artifact()
        result = engine.predict_incremental(artifact, documents=[], vectors=[])
        self.assertEqual(result.topics, [])
        self.assertEqual(artifact.topic_model.calls, 0)

    def test_select_lambda_with_single_vector_falls_back(self):
        """只有一筆文件時算不出兩兩距離——要回退，不得 raise。"""
        result = engine.select_lambda([[1.0, 0.0]], documents=["only one"])
        self.assertGreater(result.value, 0.0)
        self.assertIn("fallback", result.method)

    def test_select_lambda_subsamples_large_input(self):
        """⚠ 超過上限時抽樣：O(n²) 的距離計算會讓校準卡住。"""
        big = [[1.0, i / 1000.0] for i in range(dpmeans.PAIRWISE_SAMPLE_LIMIT + 20)]
        docs = [f"doc {i % 7} term{i % 7}" for i in range(len(big))]
        result = engine.select_lambda(big, documents=docs)
        self.assertGreater(result.value, 0.0)

    def test_quality_without_documents_returns_none(self):
        """文件缺漏時指標算不出來——回 None，不得讓整輪掃描失敗。"""
        self.assertEqual(engine._quality([0, 1], []),
                         {"coherence": None, "diversity": None})

    def test_quality_with_mismatched_lengths_returns_none(self):
        """⚠ 文件數與標籤數對不上代表上游出錯，不得硬算出一個假指標。"""
        self.assertEqual(engine._quality([0, 1, 2], ["only one"]),
                         {"coherence": None, "diversity": None})

    def test_quality_survives_coherence_failure(self):
        """⚠ coherence 算不出來時仍要回 diversity——它只是排序依據之一。"""
        from unittest import mock

        with mock.patch("backend.app.clustering.model.topic_cv_coherence_per_topic",
                        side_effect=RuntimeError("gensim unavailable")):
            quality = engine._quality([0, 0, 1], ["ski belt", "ski drive", "treadmill deck"])
        self.assertIsNone(quality["coherence"])
        self.assertIsNotNone(quality["diversity"])

    def test_quality_with_blank_documents_scores_zero_diversity(self):
        """⚠ 全空文本抽不出關鍵詞 → diversity 0，不是 None。

        兩者語意不同：None 是「算不出來」（缺文件、長度對不上），0 是「算出來
        就是最差」。排序時 None 視為最差、0 也接近最差，結果相近——但混用會讓
        「為什麼這個候選沒有分數」查不出來。
        """
        quality = engine._quality([0, 1], ["", ""])
        self.assertEqual(quality["diversity"], 0.0)


if __name__ == "__main__":
    unittest.main()
