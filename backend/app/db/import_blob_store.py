"""app_layer.import_blobs 的單一存取層（backend 寫、worker 讀）。

用途（2026-07-23 定案）：Railway 上 backend 與 worker 是**不同容器**、檔案系統不共享，
worker 找不到 backend 寫的上傳檔。兩容器共用同一個 PostgreSQL，故以本表當跨容器傳輸媒介。

契約：
- 上傳端 create_blob → append_chunk（可多次）→ finalize_blob，全程分塊，不整包進記憶體。
- worker 端 write_blob_to_path 取內容落暫存檔並驗 SHA-256，不符即 ValueError 且不留檔。
- 內容為短生命週期：worker 匯入完（含重複檔）即 delete_blob；追溯靠 raw_records.source_file_hash。

不放 job_repository 的理由：那是佇列（workflow_runs）存取層，檔案內容與佇列語意無關，
且必須確保內容永不進入 job 查詢的選欄。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from backend.app.db.connection import get_pool


# 取回內容寫檔的分塊大小（1 MiB）：與 wips_importer.file_sha256 同級，避免一次 write 大 buffer。
_WRITE_CHUNK_BYTES = 1024 * 1024


def create_blob(original_filename: str) -> int:
    """建立空內容列，回傳 blob_id；內容之後由 append_chunk 分塊補上。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_layer.import_blobs (original_filename) VALUES (%s) "
                "RETURNING blob_id",
                (original_filename,),
            )
            blob_id = cur.fetchone()[0]
        conn.commit()
    return int(blob_id)


def append_chunk(blob_id: int, chunk: bytes) -> None:
    """把一塊內容 append 到既有 bytea 尾端（content || chunk），不整包重寫。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_layer.import_blobs SET content = content || %s WHERE blob_id = %s",
                (chunk, blob_id),
            )
        conn.commit()


def finalize_blob(blob_id: int, *, file_hash: str, byte_size: int) -> None:
    """上傳完成後落款 SHA-256 與位元組數，供 worker 端完整性驗證。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_layer.import_blobs SET file_hash = %s, byte_size = %s "
                "WHERE blob_id = %s",
                (file_hash, byte_size, blob_id),
            )
        conn.commit()


def write_blob_to_path(blob_id: int, target: Path, *, expected_hash: str) -> None:
    """取回內容寫入 target 並驗 SHA-256；blob 不存在或 hash 不符即 ValueError 且刪除半成品檔。

    以 psycopg 的伺服器端 cursor 逐塊取回（substring），避免整份 bytea 一次進 client 記憶體；
    每塊寫檔的同時累加 hash，不需二次讀檔。
    """
    target = Path(target)
    hasher = hashlib.sha256()
    total = 0
    try:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                with target.open("wb") as handle:
                    offset = 1  # SQL substring 由 1 起算
                    while True:
                        cur.execute(
                            "SELECT substring(content FROM %s FOR %s) "
                            "FROM app_layer.import_blobs WHERE blob_id = %s",
                            (offset, _WRITE_CHUNK_BYTES, blob_id),
                        )
                        row = cur.fetchone()
                        if row is None:
                            raise ValueError(f"import blob not found: {blob_id}")
                        chunk = bytes(row[0] or b"")
                        if not chunk:
                            break
                        handle.write(chunk)
                        hasher.update(chunk)
                        total += len(chunk)
                        offset += len(chunk)
        if total == 0:
            raise ValueError(f"import blob is empty: {blob_id}")
        if hasher.hexdigest() != expected_hash:
            raise ValueError("import blob hash mismatch")
    except BaseException:
        # 驗證失敗不留半成品暫存檔，避免後續誤用。
        target.unlink(missing_ok=True)
        raise


def delete_blob(blob_id: int) -> None:
    """刪除內容列；匯入結束（成功或重複檔）即呼叫，不長期佔用 DB 空間。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_layer.import_blobs WHERE blob_id = %s", (blob_id,))
        conn.commit()
