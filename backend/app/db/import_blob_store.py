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
import logging
from pathlib import Path

from backend.app.db.connection import get_pool


LOGGER = logging.getLogger(__name__)

# 孤兒掃描的預設保留時數：blob 建立未滿此時數者一律不刪，避免刪到「剛 create_blob、
# 還沒建 job（或 job 還沒把 blob_id 寫進 request_json）」的上傳中內容。可由呼叫端覆寫。
DEFAULT_ORPHAN_MIN_AGE_HOURS = 24


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
    """刪除內容列；匯入結束（成功或重複檔）即呼叫，不長期佔用 DB 空間。

    冪等：DELETE 不存在的 blob_id 不報錯（成功路徑可能已刪過，終結態清理重複刪也安全）。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_layer.import_blobs WHERE blob_id = %s", (blob_id,))
        conn.commit()


def cleanup_orphan_blobs(*, min_age_hours: float = DEFAULT_ORPHAN_MIN_AGE_HOURS,
                         dry_run: bool = False) -> dict[str, object]:
    """掃描並刪除「無主」import_blobs：無任何非終結態 patent_import job 引用、且夠舊者。

    無主判定（雙重保護，缺一不可）：
    1. 該 blob_id 未被任何**非終結態**（status IN ('queued','running')）的 patent_import job
       的 request_json->>'blob_id' 引用。仍會被重試（queued/running）的 job，其 blob 必須留著，
       否則重試取不到內容——這是最高風險，故排除的是「還活著」的 job，不是全部 job。
    2. created_at 已超過 min_age_hours。保護「剛 create_blob、job 還沒建（或 blob_id 還沒寫進
       request_json）」的上傳中內容——那種 blob 此刻確實無 job 引用，但不是孤兒，時間門檻擋掉它。

    只處理 run_type='patent_import'：其他 job 型別不持有 blob。
    dry_run=True 時只回報將刪的 blob_id 與筆數，不真的刪，供人工先確認。
    回傳 {"deleted_count", "blob_ids", "dry_run", "min_age_hours"}。
    """
    if min_age_hours < 0:
        raise ValueError("min_age_hours must be >= 0")
    # 引用子查詢只認「活著的」patent_import job：終結態（succeeded/failed/cancelled）的 job
    # 不再需要 blob，故不納入保護；活著的（queued/running）才是「重試/進行中會用到」的引用。
    # make_interval 用秒數（secs）才能支援小數時數（測試常用小門檻），hours 只吃整數。
    interval_seconds = float(min_age_hours) * 3600.0
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT b.blob_id
                FROM app_layer.import_blobs AS b
                WHERE b.created_at < now() - make_interval(secs => %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM app_layer.workflow_runs AS r
                      WHERE r.run_type = 'patent_import'
                        AND r.status IN ('queued', 'running')
                        AND (r.request_json->>'blob_id')::bigint = b.blob_id
                  )
                ORDER BY b.blob_id
                """,
                (interval_seconds,),
            )
            blob_ids = [int(row[0]) for row in cur.fetchall()]
            if not dry_run and blob_ids:
                cur.execute(
                    "DELETE FROM app_layer.import_blobs WHERE blob_id = ANY(%s)",
                    (blob_ids,),
                )
        if not dry_run:
            conn.commit()
    LOGGER.info(
        "orphan import_blobs scan: %s %d blob(s) (min_age_hours=%s)",
        "would delete" if dry_run else "deleted", len(blob_ids), min_age_hours)
    return {
        "deleted_count": 0 if dry_run else len(blob_ids),
        "blob_ids": blob_ids,
        "dry_run": dry_run,
        "min_age_hours": min_age_hours,
    }


def _build_cli_parser():
    """孤兒掃描 CLI 參數解析器（供 python -m 手動觸發）。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Scan and delete orphan app_layer.import_blobs (no active patent_import job).")
    parser.add_argument(
        "--min-age-hours", type=float, default=DEFAULT_ORPHAN_MIN_AGE_HOURS,
        help="只刪 created_at 超過此時數的 blob，保護上傳中／剛建的內容（預設 24）。")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只列出將刪的 blob_id，不真的刪除，供人工先確認。")
    return parser


def main() -> None:
    """手動觸發孤兒掃描：uv run python -m backend.app.db.import_blob_store [--dry-run]。"""
    logging.basicConfig(level=logging.INFO)
    args = _build_cli_parser().parse_args()
    result = cleanup_orphan_blobs(min_age_hours=args.min_age_hours, dry_run=args.dry_run)
    verb = "would delete" if result["dry_run"] else "deleted"
    print(f"{verb} {len(result['blob_ids'])} orphan blob(s): {result['blob_ids']}")


if __name__ == "__main__":
    main()
