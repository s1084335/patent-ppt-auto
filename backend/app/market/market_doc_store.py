"""市場資料線底層存取層（derived_layer 兩表，接 0034 schema）。

- MarketDocSummaryStore：AI 摘要版本化。建摘要（數值 payload 可空）、查最新現行版、
  重跑標舊版過期（只留一個 current）、逐筆 accept 落款。**AI 產出的實際寫入內容由批2
  組裝後呼 create_summary；本層只負責版本切換與確認落款的確定性邏輯。**
- MarketDocumentStore：市場 PDF 的 metadata（內容在檔案系統，不在 DB）。記 metadata、
  依 workspace 列出、單筆取（拿 stored_filename 去刪檔）、刪除。

SQL 只留在本模組（沿 market_store.py 慣例）。
"""
from __future__ import annotations

from typing import Any

from backend.app.db.connection import get_pool


class MarketDocSummaryStore:
    """market_doc_summaries 存取層（版本化摘要，供批2／批3 呼叫）。"""

    def create_summary(
        self,
        workspace_id: int,
        *,
        payload_json: dict[str, Any] | None = None,
        narrative: str | None = None,
        source_document: str | None = None,
    ) -> int:
        """建立新摘要並設為現行版；同 workspace 既有現行版一律轉 superseded。

        version 取該 workspace 目前最大版本 +1（首建為 1）。payload_json 可為 None——
        數值薄弱時只留 narrative（規格鐵律：市場資料是輔助，數值欄可空）。accepted_at
        建立時為 NULL（未確認），待逐筆 accept 落款。回傳新 summary_id。
        """
        from psycopg.types.json import Jsonb

        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                # 先把既有現行版標過期：確保任一時刻同 workspace 只有一個 current。
                cur.execute(
                    "UPDATE derived_layer.market_doc_summaries SET status = 'superseded' "
                    "WHERE workspace_id = %s AND status = 'current'",
                    (workspace_id,),
                )
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 "
                    "FROM derived_layer.market_doc_summaries WHERE workspace_id = %s",
                    (workspace_id,),
                )
                next_version = int(cur.fetchone()[0])
                cur.execute(
                    "INSERT INTO derived_layer.market_doc_summaries "
                    "(workspace_id, version, status, payload_json, narrative, source_document) "
                    "VALUES (%s, %s, 'current', %s, %s, %s) RETURNING summary_id",
                    (
                        workspace_id,
                        next_version,
                        Jsonb(payload_json) if payload_json is not None else None,
                        narrative,
                        source_document,
                    ),
                )
                summary_id = cur.fetchone()[0]
            conn.commit()
        return int(summary_id)

    def get_current(self, workspace_id: int) -> dict[str, Any] | None:
        """回傳該 workspace 的最新現行版摘要；無則 None（降級：無市場資料整區隱藏）。"""
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary_id, workspace_id, version, status, payload_json, "
                    "       narrative, source_document, accepted_at, created_at "
                    "FROM derived_layer.market_doc_summaries "
                    "WHERE workspace_id = %s AND status = 'current' "
                    "ORDER BY version DESC LIMIT 1",
                    (workspace_id,),
                )
                row = cur.fetchone()
        return _summary_row_to_dict(row) if row is not None else None

    def get_accepted_current(self, workspace_id: int) -> dict[str, Any] | None:
        """回傳該 workspace 「現行版且已確認」的摘要；否則 None。

        報表／PPT 只讀此結果——現行版尚未 accept（accepted_at IS NULL）即拿不到，
        未確認草稿實體上進不了報表（沿文獻備註／中文名護欄的實體隔離精神）。
        """
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary_id, workspace_id, version, status, payload_json, "
                    "       narrative, source_document, accepted_at, created_at "
                    "FROM derived_layer.market_doc_summaries "
                    "WHERE workspace_id = %s AND status = 'current' "
                    "  AND accepted_at IS NOT NULL "
                    "ORDER BY version DESC LIMIT 1",
                    (workspace_id,),
                )
                row = cur.fetchone()
        return _summary_row_to_dict(row) if row is not None else None

    def accept(self, summary_id: int) -> bool:
        """逐筆確認落款 accepted_at（未確認為 NULL）；回傳是否真的落到款。

        以 now() 落款（不覆蓋既有落款——已確認過的重按不改時間，僅未確認者落款）。
        """
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE derived_layer.market_doc_summaries SET accepted_at = now() "
                    "WHERE summary_id = %s AND accepted_at IS NULL",
                    (summary_id,),
                )
                accepted = cur.rowcount
            conn.commit()
        return bool(accepted)


class MarketDocumentStore:
    """market_documents 存取層（PDF metadata，內容在檔案系統）。"""

    def record_document(
        self,
        workspace_id: int,
        *,
        original_filename: str,
        stored_filename: str,
        file_hash: str,
        byte_size: int,
    ) -> int:
        """記一份市場 PDF 的 metadata（內容已落檔案系統）；回傳 document_id。

        byte_size 由上傳端串流累計得出——內容不在 DB，無法事後 length() 推導。
        """
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO derived_layer.market_documents "
                    "(workspace_id, original_filename, stored_filename, file_hash, byte_size) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING document_id",
                    (workspace_id, original_filename, stored_filename, file_hash, byte_size),
                )
                document_id = cur.fetchone()[0]
            conn.commit()
        return int(document_id)

    def list_documents(self, workspace_id: int) -> list[dict[str, Any]]:
        """列出某 workspace 的市場 PDF metadata（新到舊）。"""
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT document_id, original_filename, stored_filename, file_hash, "
                    "       byte_size, uploaded_at "
                    "FROM derived_layer.market_documents "
                    "WHERE workspace_id = %s ORDER BY document_id DESC",
                    (workspace_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "document_id": int(r[0]),
                "original_filename": r[1],
                "stored_filename": r[2],
                "file_hash": r[3],
                "byte_size": int(r[4]),
                "uploaded_at": r[5].isoformat() if r[5] is not None else None,
            }
            for r in rows
        ]

    def get_document(self, workspace_id: int, document_id: int) -> dict[str, Any] | None:
        """單筆取回 metadata（含 stored_filename 供刪檔定位）；查無回 None。

        帶 workspace_id 條件：不允許以 document_id 跨 workspace 取用。
        """
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT document_id, original_filename, stored_filename, file_hash, "
                    "       byte_size, uploaded_at "
                    "FROM derived_layer.market_documents "
                    "WHERE workspace_id = %s AND document_id = %s",
                    (workspace_id, document_id),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {
            "document_id": int(row[0]),
            "original_filename": row[1],
            "stored_filename": row[2],
            "file_hash": row[3],
            "byte_size": int(row[4]),
            "uploaded_at": row[5].isoformat() if row[5] is not None else None,
        }

    def delete_document(self, document_id: int, *, workspace_id: int | None = None) -> bool:
        """刪除 metadata 列；回傳是否真的刪到（實體檔由呼叫端另刪）。

        workspace_id 為 None 時只依 document_id 刪——僅供上傳失敗時清自己剛建的列。
        使用者觸發的刪除一律帶 workspace_id，避免跨 workspace 刪除。
        """
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                if workspace_id is None:
                    cur.execute(
                        "DELETE FROM derived_layer.market_documents WHERE document_id = %s",
                        (document_id,),
                    )
                else:
                    cur.execute(
                        "DELETE FROM derived_layer.market_documents "
                        "WHERE document_id = %s AND workspace_id = %s",
                        (document_id, workspace_id),
                    )
                deleted = cur.rowcount
            conn.commit()
        return bool(deleted)


def _summary_row_to_dict(row: tuple) -> dict[str, Any]:
    """摘要列 → dict（accepted_at／created_at 轉 ISO 字串，payload_json 原樣）。"""
    return {
        "summary_id": int(row[0]),
        "workspace_id": int(row[1]),
        "version": int(row[2]),
        "status": row[3],
        "payload_json": row[4],
        "narrative": row[5],
        "source_document": row[6],
        "accepted_at": row[7].isoformat() if row[7] is not None else None,
        "created_at": row[8].isoformat() if row[8] is not None else None,
    }
