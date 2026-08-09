"""DP-Means artifact schema 與新舊版本讀取（tasks 1.3／2.3 Red）。

## 相容策略（2026-08-09 使用者定案：feature flag 並存）

新舊引擎同時存在，預設仍走 MiniBatchKMeans。因此 artifact **必須自己說出它是
哪個演算法產的**——否則 DP-Means 的 run 會被當成 KMeans 讀，中心格式對不上，
而且是那種「載入成功、分群結果卻莫名其妙」的靜默錯。

⚠ 舊 artifact 沒有 `algorithm` 欄位（pickle 存的是當時的 dataclass）。讀取時
補預設 `minibatch_kmeans`，**不得 raise**——它們是既有正式資料，讀不到就等於
現有 workspace 全部要重跑，那不是這個 change 的範圍。
"""
from __future__ import annotations

import pickle
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.clustering import artifacts, dpmeans


def _dpmeans_state() -> dpmeans.ClusterState:
    return dpmeans.fit([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]], lambda_=0.5)


def _artifact(**overrides):
    base = dict(
        workspace_id=3,
        source_field="wips_independent_claims",
        run_id=7,
        artifact_version=artifacts.ARTIFACT_SCHEMA_VERSION,
        reducer=None,
        topic_model=None,
        embedding_model="AI-Growth-Lab/PatentSBERTa",
        embedding_model_version="v1",
        preprocessing_version="v1",
    )
    base.update(overrides)
    return artifacts.WorkspaceTopicArtifact(**base)


class SchemaTests(unittest.TestCase):
    def test_algorithm_defaults_to_kmeans(self):
        """⚠ 預設值必須是舊引擎——feature flag 並存期間，沒特別指定就是舊的。"""
        self.assertEqual(_artifact().algorithm, artifacts.ALGORITHM_KMEANS)

    def test_dpmeans_artifact_carries_state(self):
        state = _dpmeans_state()
        art = _artifact(algorithm=artifacts.ALGORITHM_DPMEANS,
                        dpmeans_state=artifacts.serialize_dpmeans_state(state, lambda_=0.5))
        self.assertEqual(art.algorithm, artifacts.ALGORITHM_DPMEANS)
        self.assertEqual(len(art.dpmeans_state["centers"]), len(state.centers))

    def test_schema_version_is_bumped(self):
        """schema 變了版本要跟著變，否則舊 run 分不出來。"""
        self.assertGreaterEqual(artifacts.ARTIFACT_SCHEMA_VERSION, 2)


class StateRoundTripTests(unittest.TestCase):
    """DP-Means 狀態存讀一致——它是純資料，不該 pickle sklearn 物件。"""

    def test_round_trip_preserves_centers_and_counts(self):
        state = _dpmeans_state()
        payload = artifacts.serialize_dpmeans_state(state, lambda_=0.5)
        restored, lambda_ = artifacts.deserialize_dpmeans_state(payload)
        self.assertEqual(restored.counts, state.counts)
        for a, b in zip(restored.centers, state.centers):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=9)
        self.assertAlmostEqual(lambda_, 0.5, places=9)

    def test_round_trip_keeps_incremental_usable(self):
        """⚠ 存讀之後還要能繼續增量——這才是保存狀態的目的。"""
        state = _dpmeans_state()
        payload = artifacts.serialize_dpmeans_state(state, lambda_=0.5)
        restored, lambda_ = artifacts.deserialize_dpmeans_state(payload)
        updated = dpmeans.partial_fit(restored, [[0.98, 0.02]], lambda_=lambda_)
        self.assertEqual(updated.labels, [0])
        self.assertEqual(updated.new_center_indexes, [])

    def test_payload_is_plain_json_types(self):
        """只用基本型別：artifact 要能被檢視與比對，不是黑盒 pickle。"""
        payload = artifacts.serialize_dpmeans_state(_dpmeans_state(), lambda_=0.5)
        import json

        json.dumps(payload)   # 不可序列化就會在這裡炸


class LegacyArtifactTests(unittest.TestCase):
    """舊 artifact（pickle 時還沒有 algorithm 欄）要能照讀。"""

    def test_legacy_pickle_loads_as_kmeans(self):
        """⚠ 模擬方式：建正常實例後**刪掉新欄位**再 pickle。

        dataclass 的 pickle 存的是 `__dict__`，舊檔案裡就是沒有那兩個 key——
        刪掉屬性能精準重現這個狀態。（用假 class 名 pickle 行不通：反序列化時
        找不到那個 qualname。）
        """
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.pkl"
            legacy = _artifact(artifact_version=1)
            del legacy.__dict__["algorithm"]
            del legacy.__dict__["dpmeans_state"]
            with path.open("wb") as handle:
                pickle.dump(legacy, handle)
            loaded = artifacts.load_artifact(path)
            self.assertEqual(loaded.algorithm, artifacts.ALGORITHM_KMEANS)
            self.assertIsNone(loaded.dpmeans_state)

    def test_new_artifact_round_trips_through_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "new.pkl"
            art = _artifact(algorithm=artifacts.ALGORITHM_DPMEANS,
                            dpmeans_state=artifacts.serialize_dpmeans_state(
                                _dpmeans_state(), lambda_=0.42))
            digest = artifacts.save_artifact(art, path)
            loaded = artifacts.load_artifact(path, expected_hash=digest)
            self.assertEqual(loaded.algorithm, artifacts.ALGORITHM_DPMEANS)
            _, lambda_ = artifacts.deserialize_dpmeans_state(loaded.dpmeans_state)
            self.assertAlmostEqual(lambda_, 0.42, places=9)

    def test_hash_mismatch_still_rejected(self):
        """⚠ 對照組：新增欄位不得弱化既有的完整性檢查。"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.pkl"
            artifacts.save_artifact(_artifact(), path)
            with self.assertRaises(ValueError):
                artifacts.load_artifact(path, expected_hash="deadbeef")


class RunMetadataTests(unittest.TestCase):
    """run metadata 要記得住這次用了什麼演算法與 lambda（CLU-008）。"""

    def test_metadata_records_algorithm_and_lambda(self):
        result = dpmeans.derive_lambda([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
        meta = artifacts.build_run_metadata(
            algorithm=artifacts.ALGORITHM_DPMEANS, lambda_result=result,
            pca_normalized=True, topics_before=5, topics_after=7)
        self.assertEqual(meta["algorithm"], artifacts.ALGORITHM_DPMEANS)
        self.assertEqual(meta["lambda"]["value"], result.value)
        self.assertEqual(meta["lambda"]["method"], result.method)
        self.assertEqual(meta["lambda"]["version"], result.version)
        self.assertTrue(meta["pca_l2_normalized"])
        self.assertEqual(meta["topics_new"], 2, "新舊主題數差要記，才看得出長出幾個")

    def test_metadata_for_kmeans_has_no_lambda(self):
        """⚠ 舊引擎不該被硬塞一個假的 lambda——沒有就是沒有。"""
        meta = artifacts.build_run_metadata(algorithm=artifacts.ALGORITHM_KMEANS)
        self.assertEqual(meta["algorithm"], artifacts.ALGORITHM_KMEANS)
        self.assertIsNone(meta.get("lambda"))


if __name__ == "__main__":
    unittest.main()
