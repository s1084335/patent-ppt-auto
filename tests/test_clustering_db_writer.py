"""測試 embedding DB writer 的對齊、向量格式與 hash。"""

from __future__ import annotations

import unittest

from backend.app.clustering.db_writer import (
    PatentEmbeddingSource,
    build_embedding_records,
    hash_embedding_vector,
    vector_to_pgvector,
)
from backend.app.clustering.model import DocumentEmbedding, resolve_patent_number
from backend.app.clustering.preprocessing import process_patent_text


class ClusteringDbWriterTests(unittest.TestCase):
    """確認 writer 不會混淆專利號或錯置 patent-to-vector 關係。"""

    def test_build_embedding_records_keeps_patent_identity(self) -> None:
        """寫入紀錄必須保留 core id、授權公告號優先序與 768 維向量。"""
        identity = resolve_patent_number({"授權公告號": "US123B2", "申請號": "US-A-1"})
        source = PatentEmbeddingSource(7, "A" * 80, identity)
        processed = process_patent_text(source.source_text, row_number=7)
        processed.chunk_count = 1
        processed.chunk_token_counts = [20]
        embedding = DocumentEmbedding(
            row_number=7,
            status="usable",
            vector=[0.25] * 768,
            vector_dim=768,
            chunk_count=1,
            chunk_weights=[1.0],
            aggregation_method="weighted_mean",
            patent_number="US123B2",
            patent_number_type="grant_publication_number",
        )

        records = build_embedding_records(
            sources=[source],
            processed=[processed],
            embeddings=[embedding],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"].patent_id, 7)
        self.assertEqual(records[0]["source"].identity.patent_number, "US123B2")
        self.assertTrue(records[0]["vector_text"].startswith("[0.25,"))
        self.assertEqual(len(records[0]["vector_hash"]), 64)

    def test_vector_helpers_reject_wrong_dimension(self) -> None:
        """pgvector 文字與 hash 都不得接受非 768 維輸入。"""
        with self.assertRaises(ValueError):
            vector_to_pgvector([0.0])
        with self.assertRaises(ValueError):
            hash_embedding_vector([0.0])

    def test_build_embedding_records_rejects_misaligned_patent_id(self) -> None:
        """來源 core id 與 embedding row 不一致時必須停止，不能錯綁文檔。"""
        identity = resolve_patent_number({"申請號": "US-A-1"})
        source = PatentEmbeddingSource(7, "A" * 80, identity)
        processed = process_patent_text(source.source_text, row_number=8)
        embedding = DocumentEmbedding(
            row_number=8,
            status="usable",
            vector=[0.25] * 768,
            vector_dim=768,
            chunk_count=1,
            chunk_weights=[1.0],
            aggregation_method="weighted_mean",
        )

        with self.assertRaises(ValueError):
            build_embedding_records(
                sources=[source],
                processed=[processed],
                embeddings=[embedding],
            )


if __name__ == "__main__":
    unittest.main()
