"""app_layer.workspace_documents 的單一存取層（技術文獻長期保存）。

用途：市場研究線的使用者上傳文獻（PDF），供 CLI 推導產品定義與市場證據。與
import_blobs（用完即刪的匯入暫存）**物理分離**——本表內容長期保存，刪除只由使用者主動
觸發或 workspace 連帶 CASCADE。

契約：
- 上傳端 create_document → append_chunk（可多次）→ finalize_document，全程分塊，
  不整包進記憶體（沿 import_blob_store 的串流語意）。
- ⚠ **list_documents 絕不 SELECT content**：只回 metadata，大小以 length(content) 推導。
  列表可能一次回多份數 MB 的 PDF，選了 content 等於每次列表都把全部內容拉回 backend。
- read_document 為單筆取用（Companion 落本機暫存檔給 CLI 讀），才會碰 content。
"""
from __future__ import annotations

from typing import Any

from backend.app.db.connection import get_pool


def create_document(workspace_id: int, original_filename: str) -> int:
    """建立空內容列，回傳 document_id；內容之後由 append_chunk 分塊補上。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_layer.workspace_documents (workspace_id, original_filename) "
                "VALUES (%s, %s) RETURNING document_id",
                (workspace_id, original_filename),
            )
            document_id = cur.fetchone()[0]
        conn.commit()
    return int(document_id)


def append_chunk(document_id: int, chunk: bytes) -> None:
    """把一塊內容 append 到既有 bytea 尾端（content || chunk），不整包重寫。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_layer.workspace_documents SET content = content || %s "
                "WHERE document_id = %s",
                (chunk, document_id),
            )
        conn.commit()


def finalize_document(document_id: int, *, file_hash: str) -> None:
    """上傳完成後落款 SHA-256，供取用端驗證完整性。

    不落 byte_size：length(content) 直接可得，不重複存可推導的值（沿 0024 精簡口徑）。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_layer.workspace_documents SET file_hash = %s WHERE document_id = %s",
                (file_hash, document_id),
            )
        conn.commit()


def list_documents(workspace_id: int) -> list[dict[str, Any]]:
    """列出某 workspace 的文獻 metadata，新到舊。

    ⚠ **護欄**：選欄明確列出且**不含 content**，大小改以 length(content) 在 DB 端算完
    只回一個整數。若選了 content，列表一次就會把該 workspace 所有 PDF（可能數十 MB）
    整包拉回 backend 記憶體——列表端點永遠不需要內容。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT document_id, original_filename, length(content) AS byte_size, "
                "       file_hash, uploaded_at "
                "FROM app_layer.workspace_documents "
                "WHERE workspace_id = %s ORDER BY document_id DESC",
                (workspace_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "document_id": int(row[0]),
            "original_filename": row[1],
            "byte_size": int(row[2] or 0),
            "file_hash": row[3],
            "uploaded_at": row[4].isoformat() if row[4] is not None else None,
        }
        for row in rows
    ]


def read_document(workspace_id: int, document_id: int) -> dict[str, Any] | None:
    """單筆取回文獻內容（供 Companion 落本機暫存檔給 CLI 讀）；查無回 None。

    帶 workspace_id 條件：文獻歸屬 workspace，不允許以 document_id 跨 workspace 取內容。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT original_filename, content, file_hash "
                "FROM app_layer.workspace_documents "
                "WHERE workspace_id = %s AND document_id = %s",
                (workspace_id, document_id),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "original_filename": row[0],
        "content": bytes(row[1] or b""),
        "file_hash": row[2],
    }


def delete_document(document_id: int, *, workspace_id: int | None = None) -> bool:
    """刪除文獻列；回傳是否真的刪到。

    workspace_id 為 None 時只依 document_id 刪——僅供上傳失敗時清自己剛建的列（此時
    id 由本次呼叫產生，不可能誤刪他人資料）。使用者觸發的刪除一律帶 workspace_id，
    避免跨 workspace 刪除。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            if workspace_id is None:
                cur.execute(
                    "DELETE FROM app_layer.workspace_documents WHERE document_id = %s",
                    (document_id,),
                )
            else:
                cur.execute(
                    "DELETE FROM app_layer.workspace_documents "
                    "WHERE document_id = %s AND workspace_id = %s",
                    (document_id, workspace_id),
                )
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)
