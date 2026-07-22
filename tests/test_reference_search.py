"""案件比對 · reference 端 embeddings 相似查件測試（真 DB）。

驗證 find_reference_candidates()：給被比對專利（subject）的 patent_id，用其 technical
embedding 以 pgvector 相似度找語意最相近的其他專利，作為比對來源（reference）候選。
排除 subject 自身；回傳依相似度排序、帶 distance 的候選清單。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("PGHOST", "127.0.0.1")

import psycopg

from backend.app.comparison.reference_search import find_reference_candidates
from backend.app.db.connection import get_connection_kwargs


def _has_embeddings() -> bool:
    """本機庫是否有 technical embeddings 可測（無則 skip，不誤判為失敗）。"""
    try:
        with psycopg.connect(**get_connection_kwargs(), connect_timeout=3) as conn:
            n = conn.execute(
                "SELECT count(*) FROM core_layer.patent_technical_embeddings"
            ).fetchone()[0]
        return n > 1
    except Exception:
        return False


def _any_subject_id() -> int:
    with psycopg.connect(**get_connection_kwargs()) as conn:
        return conn.execute(
            "SELECT patent_id FROM core_layer.patent_technical_embeddings LIMIT 1"
        ).fetchone()[0]


@unittest.skipUnless(_has_embeddings(), "本機庫無 technical embeddings")
class FindReferenceCandidatesTests(unittest.TestCase):

    def test_returns_similar_patents_excluding_self(self):
        """回傳相似候選，不含 subject 自身，數量不超過 limit。"""
        subject = _any_subject_id()
        results = find_reference_candidates(subject, limit=5)
        self.assertTrue(len(results) >= 1)
        self.assertLessEqual(len(results), 5)
        ids = [r["patent_id"] for r in results]
        self.assertNotIn(subject, ids)  # 排除自身

    def test_ordered_by_similarity(self):
        """結果依 distance 由小到大（最相似在前）。"""
        subject = _any_subject_id()
        results = find_reference_candidates(subject, limit=5)
        distances = [r["distance"] for r in results]
        self.assertEqual(distances, sorted(distances))

    def test_missing_subject_embedding_raises(self):
        """subject 無 embedding（不存在的 patent_id）→ 明確錯誤，不回空混淆。"""
        from backend.app.comparison.reference_search import ReferenceSearchError
        with self.assertRaises(ReferenceSearchError):
            find_reference_candidates(-99999, limit=5)


if __name__ == "__main__":
    unittest.main()
