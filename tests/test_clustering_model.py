from __future__ import annotations

import unittest

import numpy as np

from backend.app.clustering.model import (
    ModelConfig,
    format_patent_number,
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

if __name__ == "__main__":
    unittest.main()
