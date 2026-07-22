"""案件比對 · reference 端相似查件（用 pgvector technical embedding 找語意相近專利）。

被比對來源（subject）確定後，比對來源（reference）可由 embeddings 查詢現有專利庫，找出
與 subject 技術語意最相近的既有專利作為候選。相似度用 pgvector cosine distance（<=>），
在資料層排序取前 N（走索引，不全載記憶體），排除 subject 自身。

此處只產「候選清單」；候選是否採為 reference、要不要另行匯入，由使用者決定（設計文件
「比對來源」定案）。回傳帶 distance 供前端呈現相似程度。
"""
from __future__ import annotations

from typing import Any

TECHNICAL_EMBEDDINGS = "core_layer.patent_technical_embeddings"


class ReferenceSearchError(RuntimeError):
    """相似查件失敗（subject 無 embedding 等）。"""


def find_reference_candidates(
    subject_patent_id: int,
    limit: int = 10,
    connect_kwargs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """用 subject 的 technical embedding 找最相似的其他專利候選。

    回傳 [{patent_id, distance, patent_number, title}]，依 distance 由小到大（最相似在前），
    排除 subject 自身，最多 limit 筆。subject 無 technical embedding → ReferenceSearchError。
    """
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        # 先確認 subject 有 embedding，否則相似查無意義，明確報錯而非回空
        has = conn.execute(
            f"SELECT 1 FROM {TECHNICAL_EMBEDDINGS} WHERE patent_id = %s LIMIT 1",
            (subject_patent_id,),
        ).fetchone()
        if has is None:
            raise ReferenceSearchError(
                f"patent {subject_patent_id} 無 technical embedding，無法做相似查件")

        # 子查詢取 subject 向量；主查詢對其餘專利算 cosine distance 排序取前 N。
        # 同 patent 可能多 chunk embedding，取每 patent 最小 distance（DISTINCT ON）。
        rows = conn.execute(
            f"""
            SELECT patent_id, distance, patent_number, title FROM (
                SELECT DISTINCT ON (e.patent_id)
                    e.patent_id,
                    e.embedding_vector <=> s.vec AS distance,
                    p."授權公告號" AS patent_number,
                    p.title AS title
                FROM {TECHNICAL_EMBEDDINGS} e
                CROSS JOIN (
                    SELECT embedding_vector AS vec FROM {TECHNICAL_EMBEDDINGS}
                    WHERE patent_id = %(sid)s LIMIT 1
                ) s
                JOIN core_layer.patents p ON p.id = e.patent_id
                WHERE e.patent_id <> %(sid)s
                ORDER BY e.patent_id, distance
            ) ranked
            ORDER BY distance
            LIMIT %(lim)s
            """,
            {"sid": subject_patent_id, "lim": limit},
        ).fetchall()

    return [
        {"patent_id": r[0], "distance": float(r[1]),
         "patent_number": r[2], "title": r[3]}
        for r in rows
    ]
