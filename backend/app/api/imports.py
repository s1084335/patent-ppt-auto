"""WIPS 匯入 API：串流上傳來源檔並建立 patent_import job。

POST /api/v1/imports：以原始 body 串流接收檔案內容＋原檔名（query `filename`），分塊
計算 SHA-256、**分塊寫入 DB**（app_layer.import_blobs），容量超過 MAX_IMPORT_UPLOAD_BYTES
即 413。只允許 Web 白名單副檔名（.xlsx/.csv/.txt/.xml，不含 .mdb），拒絕 path traversal
與空檔。驗證/寫入/建 job 任一失敗都刪除本次 blob，不留孤兒內容。實際匯入由 worker
複用 import_wips_file()。第一版用 raw body 串流，不引入 python-multipart。

落 DB 而非落檔（2026-07-23 定案）：Railway 上 backend 與 worker 是**不同容器**、檔案系統
不共享（Railway volume 只能綁單一服務），worker 依 payload.path 在自己的檔案系統找不到
backend 寫的檔而失敗。兩容器本來就共用同一個 PostgreSQL，故改以 DB 傳遞內容——本機與
Railway 走同一條路徑，不再依賴 compose 的共享 volume，也不留「只有本機能跑」的分支。
串流語意保留：內容逐塊 append 進 bytea，全程不整包進記憶體（上限 200 MiB 不可整包讀）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from backend.app import settings
from backend.app.api.jobs import job_to_dict
from backend.app.db import import_blob_store, job_repository
from backend.app.importers.import_paths import validate_web_filename


router = APIRouter(tags=["imports"])


# 用途標籤白名單（2026-07-22 定案）：一般匯入 vs 案件比對匯入共用同一套機制，purpose
# 讓專利總覽可區分。與 app_layer.workspace_create.PURPOSES 同一組值（此處重列避免 API 層
# 反向依賴 app_layer；新增用途時兩處一起改）。
IMPORT_PURPOSES = ("general", "case_comparison")
DEFAULT_IMPORT_PURPOSE = "general"


@router.post("/imports")
async def create_import(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    workspace_id: int | None = Query(None, ge=1),
    new_workspace_name: str | None = Query(None, min_length=1, max_length=255),
    purpose: str = Query(DEFAULT_IMPORT_PURPOSE),
) -> dict[str, Any]:
    """串流接收上傳、分塊存進 app_layer.import_blobs 並建立 patent_import job。

    空檔、不支援副檔名（含 .mdb）、path traversal → 422；超過容量上限 → 413。任一失敗都
    刪除本次 blob。成功回新建 job（含 job_id 供輪詢 /jobs/{id}）。

    匯入帶 workspace（2026-07-22 定案）：workspace_id（既有，union 去重）或 new_workspace_name
    （新建，成員＝這次匯入專利）二選一，兩者皆給 → 422；purpose（general／case_comparison，
    非白名單 → 422）為用途標籤，一路帶進 job payload，由 worker 圈 workspace 時落
    settings_json。payload 放 blob_id、原檔名、內容 hash 與 workspace/purpose 意圖——
    **只放 blob_id 不放內容**：request_json 被每一次 job 查詢（get_job／list_jobs／claim）
    帶回，內容進 JSONB 會讓每次輪詢佇列都拖回上百 MB。
    """
    if workspace_id is not None and new_workspace_name is not None:
        raise HTTPException(
            status_code=422,
            detail="provide either workspace_id or new_workspace_name, not both",
        )
    if purpose not in IMPORT_PURPOSES:
        raise HTTPException(status_code=422, detail=f"unsupported purpose: {purpose!r}")

    max_bytes = settings.MAX_IMPORT_UPLOAD_BYTES

    # Content-Length 若存在且超限，提早 413（尚未 mkdir/寫檔）；缺漏或偽造則靠串流累計強制限制。
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_int = int(declared)
        except ValueError:
            declared_int = None
        if declared_int is not None and declared_int > max_bytes:
            raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")

    # 檔名驗證（Web 白名單、拒 .mdb、拒 traversal）先於任何落地動作。
    try:
        safe_name = validate_web_filename(filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 先建空 blob 取得 blob_id，內容再逐塊 append；DB 寫入屬阻塞 I/O，一律走 threadpool。
    blob_id = await run_in_threadpool(import_blob_store.create_blob, safe_name)
    try:
        hasher = hashlib.sha256()
        total = 0
        # 逐塊 append 進 bytea：與原本逐塊寫檔同樣不整包進記憶體，故 200 MiB 上限維持安全。
        # 每塊獨立 UPDATE（自帶 transaction），失敗時由下方 except 刪整個 blob 收尾。
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")
            hasher.update(chunk)
            await run_in_threadpool(import_blob_store.append_chunk, blob_id, chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="empty upload body")
        file_hash = hasher.hexdigest()
        # 落款 hash 與大小；worker 取檔後重算比對，不符即拒絕匯入。
        await run_in_threadpool(
            import_blob_store.finalize_blob, blob_id, file_hash=file_hash, byte_size=total
        )

        # 匯入意圖一路帶進 payload，由 worker 圈 workspace 時消費：new_workspace_name（新建）
        # 或 workspace_id（既有 union）、purpose（用途標籤）。job.workspace_id 對既有
        # workspace 直接帶上（供 /jobs 依 workspace 過濾）；新建 workspace 的 id 匯入後才知，
        # 故建 job 時為 None，由 worker 圈 workspace 後寫回 summary。
        import_payload: dict[str, Any] = {
            "blob_id": blob_id,
            "original_filename": safe_name,
            "file_hash": file_hash,
            "purpose": purpose,
        }
        if new_workspace_name is not None:
            import_payload["new_workspace_name"] = new_workspace_name
        if workspace_id is not None:
            import_payload["workspace_id"] = workspace_id
        # create_job 為同步 DB 寫入，放 threadpool 執行避免阻塞事件迴圈。
        job = await run_in_threadpool(
            job_repository.create_job,
            "patent_import",
            import_payload,
            workspace_id=workspace_id,
        )
        return job_to_dict(job)
    except BaseException:
        # 驗證/寫入/超限/建 job 任一失敗 → 刪除本次 blob，不留孤兒內容佔用 DB 空間。
        await run_in_threadpool(import_blob_store.delete_blob, blob_id)
        raise
