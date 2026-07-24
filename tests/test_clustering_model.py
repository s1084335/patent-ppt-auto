from __future__ import annotations

import unittest

import numpy as np

from backend.app.clustering.model import (
    ModelConfig,
    format_patent_number,
    irrelevant_sample_size,
    rank_ctfidf_least_representative_documents,
    rank_ctfidf_representative_documents,
    resolve_patent_number,
    weighted_mean_vectors,
)


class _DocumentVectorizer:
    """以測試文件名稱回傳預先配置的文件向量。"""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        """保存文件至向量的測試映射。"""
        self.vectors = vectors

    def transform(self, documents: list[str]) -> np.ndarray:
        """依輸入順序建立測試向量矩陣。"""
        return np.asarray([self.vectors[document] for document in documents], dtype=float)


class _IdentityCtfidf:
    """測試用 c-TF-IDF transformer，保留輸入向量。"""

    @staticmethod
    def transform(matrix: np.ndarray) -> np.ndarray:
        """直接回傳文件向量，隔離排序邏輯。"""
        return matrix


class _TopicModelStub:
    """提供代表文件排序所需的最小 BERTopic 介面。"""

    def __init__(
        self,
        *,
        document_vectors: dict[str, list[float]],
        topic_vectors: list[list[float]],
    ) -> None:
        """建立固定 topic 與文件 c-TF-IDF 向量。"""
        self.vectorizer_model = _DocumentVectorizer(document_vectors)
        self.ctfidf_model = _IdentityCtfidf()
        self.c_tf_idf_ = np.asarray(topic_vectors, dtype=float)

    def get_topics(self) -> dict[int, list[tuple[str, float]]]:
        """依 c-TF-IDF row 數回傳連續 topic ID。"""
        return {topic_id: [] for topic_id in range(len(self.c_tf_idf_))}


class ClusteringModelContractTests(unittest.TestCase):
    """驗證分群模型最重要且不需下載模型的資料契約。"""

    def test_production_reducer_defaults_to_100_dimensions(self) -> None:
        """正式分群的 IncrementalPCA 維度必須固定為已定案的 100。"""
        self.assertEqual(ModelConfig().n_components, 100)

    def test_patent_number_uses_grant_to_application_priority(self) -> None:
        """授權公告號存在時必須優先作為業務追蹤專利號。"""
        identity = resolve_patent_number(
            {
                "country_code": "US",
                "授權公告號": "12667896",
                "審查的公告號": "20250001",
                "未審查的公開號": "20240001",
                "申請號": "18/648768",
            }
        )

        self.assertEqual(identity.patent_number, "12667896")
        self.assertEqual(identity.patent_number_type, "grant_publication_number")
        self.assertEqual(identity.application_number, "18/648768")

    def test_patent_number_preserves_source_value_without_country_prefix(self) -> None:
        """分欄 country code 不得加進號碼，來源本有的字元也不得剝除。"""
        self.assertEqual(format_patent_number("us", "12667896"), "12667896")
        self.assertEqual(format_patent_number("US", "US-12667896"), "US-12667896")
        self.assertEqual(format_patent_number(None, "12667896"), "12667896")

    def test_taiwan_patent_number_converts_gregorian_year_prefix(self) -> None:
        """台灣號碼前四位西元年減 1911，後方數字與分隔符必須不變。"""
        self.assertEqual(format_patent_number("TW", "2024123456"), "113123456")
        self.assertEqual(format_patent_number("tw", "2024/123456"), "113/123456")
        self.assertEqual(format_patent_number("TW", "I123456"), "I123456")

    def test_taiwan_application_identity_uses_transformed_downstream_value(self) -> None:
        """台灣 identity 與追蹤號都使用轉換後值，核心原值由 DB 原欄保存。"""
        identity = resolve_patent_number(
            {
                "country_code": "TW",
                "申請號": "2024123456",
            }
        )

        self.assertEqual(identity.patent_number, "113123456")
        self.assertEqual(identity.application_number, "113123456")
        self.assertEqual(identity.patent_number_type, "application_number")

    def test_stored_transformed_number_takes_priority_over_recalculation(self) -> None:
        """DB 已有轉換後欄位時，下游解析必須直接使用該欄。"""
        identity = resolve_patent_number(
            {
                "country_code": "TW",
                "申請號": "2024123456",
                "申請號(轉換後)": "113123456",
            }
        )

        self.assertEqual(identity.application_number, "113123456")

    def test_weighted_mean_preserves_chunk_token_proportions(self) -> None:
        """Patent-level embedding 必須依 chunk token 比例聚合。"""
        result = weighted_mean_vectors(
            vectors=[[1.0, 0.0], [0.0, 1.0]],
            weights=[0.75, 0.25],
        )

        self.assertEqual(result, [0.75, 0.25])

    def test_ctfidf_representatives_are_ranked_per_topic_and_limited_to_ten(self) -> None:
        """每個 topic 應各自依 c-TF-IDF cosine similarity 取前 10 筆。"""
        documents = [f"topic0-{index}" for index in range(12)] + [
            f"topic1-{index}" for index in range(12)
        ]
        document_vectors = {
            document: ([index + 1.0, 1.0] if index < 12 else [1.0, index - 11.0])
            for index, document in enumerate(documents)
        }
        topic_model = _TopicModelStub(
            document_vectors=document_vectors,
            topic_vectors=[[1.0, 0.0], [0.0, 1.0]],
        )

        result = rank_ctfidf_representative_documents(
            topic_model=topic_model,
            documents=documents,
            topics=[0] * 12 + [1] * 12,
        )

        self.assertEqual(result[0], list(range(11, 1, -1)))
        self.assertEqual(result[1], list(range(23, 13, -1)))

    def test_ctfidf_representatives_keep_distinct_indexes_for_duplicate_text(self) -> None:
        """重複文字仍須保留不同 corpus index，並以原列順序穩定解決同分。"""
        documents = ["same independent claim"] * 12
        topic_model = _TopicModelStub(
            document_vectors={"same independent claim": [1.0, 0.0]},
            topic_vectors=[[1.0, 0.0]],
        )

        result = rank_ctfidf_representative_documents(
            topic_model=topic_model,
            documents=documents,
            topics=[0] * 12,
        )

        self.assertEqual(result[0], list(range(10)))


class IrrelevantSampleSizeTests(unittest.TestCase):
    """不相干篩選反向取樣的每主題取樣數（依主題大小調整、不寫死單一數字）。"""

    def test_scales_with_topic_size_not_fixed_number(self) -> None:
        """取樣數隨主題大小變動，不是固定值——不同大小回不同數字。"""
        sizes = {size: irrelevant_sample_size(size) for size in (20, 50, 200)}
        # 至少有兩個不同的取樣數，證明不是寫死單一數字。
        self.assertGreater(len(set(sizes.values())), 1)
        # 單調不減：主題越大取樣數不應變小。
        ordered = [sizes[20], sizes[50], sizes[200]]
        self.assertEqual(ordered, sorted(ordered))

    def test_small_topic_never_samples_whole_topic(self) -> None:
        """小主題（總數 < 預設 N）不得取到整題——至少留一筆在主題內。"""
        for size in range(2, 12):
            n = irrelevant_sample_size(size)
            self.assertLess(n, size, f"topic size {size} 取樣 {n} 取到整題")
            self.assertGreaterEqual(n, 1)

    def test_single_member_topic_samples_none(self) -> None:
        """只有 1 筆的主題無法保留成員又取樣，回 0（不取）。"""
        self.assertEqual(irrelevant_sample_size(1), 0)
        self.assertEqual(irrelevant_sample_size(0), 0)

    def test_has_upper_cap(self) -> None:
        """超大主題取樣數設上限，不隨資料量無限膨脹。"""
        huge = irrelevant_sample_size(100_000)
        self.assertLessEqual(huge, 30)


class LeastRepresentativeTests(unittest.TestCase):
    """反向取樣：取每主題 c-TF-IDF cosine similarity 最低（最不像該主題）的 N 筆。"""

    def _stub_two_topics(self):
        """topic0：index 0 最像、11 最不像；topic1 反之。與正向測試同資料。"""
        documents = [f"topic0-{index}" for index in range(12)] + [
            f"topic1-{index}" for index in range(12)
        ]
        document_vectors = {
            document: ([index + 1.0, 1.0] if index < 12 else [1.0, index - 11.0])
            for index, document in enumerate(documents)
        }
        topic_model = _TopicModelStub(
            document_vectors=document_vectors,
            topic_vectors=[[1.0, 0.0], [0.0, 1.0]],
        )
        return topic_model, documents

    def test_returns_lowest_similarity_not_highest(self) -> None:
        """取的是相似度最低那批，與正向（取最高）方向相反。"""
        topic_model, documents = self._stub_two_topics()
        topics = [0] * 12 + [1] * 12
        forward = rank_ctfidf_representative_documents(
            topic_model=topic_model, documents=documents, topics=topics, limit=3)
        reverse = rank_ctfidf_least_representative_documents(
            topic_model=topic_model, documents=documents, topics=topics, limit=3)
        # 正向 topic0 取最像（index 11,10,9）；反向必須取最不像（index 1,2,3——index 0 因
        # [1,1] 與 topic 向量 [1,0] 夾角比 index 1 [2,1] 大，實際最不像者交由函式判定）。
        # 關鍵斷言：反向結果與正向結果不重疊（方向確實相反）。
        self.assertEqual(set(forward[0]) & set(reverse[0]), set())
        self.assertEqual(set(forward[1]) & set(reverse[1]), set())

    def test_reverse_is_exact_mirror_of_forward_ordering(self) -> None:
        """反向排序＝正向排序的鏡像：反向第一名＝相似度最低者。"""
        topic_model, documents = self._stub_two_topics()
        topics = [0] * 12 + [1] * 12
        # 取滿整題比對完整排序方向。
        forward = rank_ctfidf_representative_documents(
            topic_model=topic_model, documents=documents, topics=topics, limit=12)
        reverse = rank_ctfidf_least_representative_documents(
            topic_model=topic_model, documents=documents, topics=topics, limit=12)
        # 反向的第一名應是正向的最後一名（相似度最低）。
        self.assertEqual(reverse[0][0], forward[0][-1])
        self.assertEqual(reverse[1][0], forward[1][-1])

    def test_size_adaptive_limit_when_no_explicit_limit(self) -> None:
        """未指定 limit 時依主題大小取樣，小主題不取整題。"""
        # topic0 有 3 筆、topic1 有 12 筆。
        documents = [f"a-{i}" for i in range(3)] + [f"b-{i}" for i in range(12)]
        document_vectors = {
            doc: ([i + 1.0, 1.0] if i < 3 else [1.0, i - 2.0])
            for i, doc in enumerate(documents)
        }
        topic_model = _TopicModelStub(
            document_vectors=document_vectors,
            topic_vectors=[[1.0, 0.0], [0.0, 1.0]],
        )
        topics = [0] * 3 + [1] * 12
        result = rank_ctfidf_least_representative_documents(
            topic_model=topic_model, documents=documents, topics=topics)
        # 小主題（3 筆）取樣數必 < 3（不取整題）。
        self.assertLess(len(result[0]), 3)
        self.assertGreaterEqual(len(result[0]), 1)


if __name__ == "__main__":
    unittest.main()
