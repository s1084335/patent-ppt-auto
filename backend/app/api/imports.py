"""WIPS 匯入 API：串流上傳來源檔並建立 patent_import job。

POST /api/v1/imports：以原始 body 串流接收檔案內容＋原檔名（query `filename`），分塊
計算 SHA-256、分塊寫入受控目錄 `data/imports/<uuid>/`，容量超過 MAX_IMPORT_UPLOAD_BYTES
即 413。只允許 Web 白名單副檔名（.xlsx/.csv/.txt/.xml，不含 .mdb），拒絕 path traversal
與空檔。驗證/寫檔/建 job 任一失敗都清除本次 UUID 目錄，不留孤兒檔。實際匯入由 worker
複用 import_wips_file()。第一版用 raw body 串流，不引入 python-multipart。
"""
from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from backend.app import settings
from backend.app.api.jobs import job_to_dict
from backend.app.db import job_repository
from backend.app.importers.import_paths import (
    imports_root,
    is_within_imports_root,
    remove_import_dir,
    validate_web_filename,
)


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
    """串流接收上傳、落地到 data/imports/<uuid>/ 並建立 patent_import job。

    空檔、不支援副檔名（含 .mdb）、path traversal → 422；超過容量上限 → 413。任一失敗都
    清除本次 UUID 目錄。成功回新建 job（含 job_id 供輪詢 /jobs/{id}）。

    匯入帶 workspace（2026-07-22 定案）：workspace_id（既有，union 去重）或 new_workspace_name
    （新建，成員＝這次匯入專利）二選一，兩者皆給 → 422；purpose（general／case_comparison，
    非白名單 → 422）為用途標籤，一路帶進 job payload，由 worker 圈 workspace 時落
    settings_json。payload 放受控路徑、原檔名、內容 hash 與 workspace/purpose 意圖。
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

    # server 產生的 uuid 目錄，隔離每次上傳並杜絕使用者可控目錄成分。
    import_id = uuid4().hex
    target_dir = imports_root() / import_id
    target_path = target_dir / safe_name
    # mkdir 屬阻塞 I/O，放 threadpool 執行，避免卡事件迴圈。
    await run_in_threadpool(target_dir.mkdir, parents=True, exist_ok=True)
    try:
        if not is_within_imports_root(target_path):
            raise HTTPException(status_code=422, detail="resolved path escapes imports directory")

        hasher = hashlib.sha256()
        total = 0
        # anyio async file：分塊寫入在 threadpool 進行，不在 async endpoint 直接 write_bytes 大檔。
        async with await anyio.open_file(target_path, "wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")
                hasher.update(chunk)
                await handle.write(chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="empty upload body")
        file_hash = hasher.hexdigest()

        # 匯入意圖一路帶進 payload，由 worker 圈 workspace 時消費：new_workspace_name（新建）
        # 或 workspace_id（既有 union）、purpose（用途標籤）。job.workspace_id 對既有
        # workspace 直接帶上（供 /jobs 依 workspace 過濾）；新建 workspace 的 id 匯入後才知，
        # 故建 job 時為 None，由 worker 圈 workspace 後寫回 summary。
        import_payload: dict[str, Any] = {
            "path": str(target_path),
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
        # 驗證/寫檔/超限/建 job 任一失敗 → 清除本次 UUID 目錄（只刪 imports root 下的本次目錄）。
        await run_in_threadpool(remove_import_dir, target_dir)
        raise
