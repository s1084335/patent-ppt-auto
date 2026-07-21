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


@router.post("/imports")
async def create_import(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
) -> dict[str, Any]:
    """串流接收上傳、落地到 data/imports/<uuid>/ 並建立 patent_import job。

    空檔、不支援副檔名（含 .mdb）、path traversal → 422；超過容量上限 → 413。任一失敗都
    清除本次 UUID 目錄。成功回新建 job（含 job_id 供輪詢 /jobs/{id}）；payload 只放受控
    路徑、原檔名與內容 hash。
    """
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

        # create_job 為同步 DB 寫入，放 threadpool 執行避免阻塞事件迴圈。
        job = await run_in_threadpool(
            job_repository.create_job,
            "patent_import",
            {
                "path": str(target_path),
                "original_filename": safe_name,
                "file_hash": file_hash,
            },
            workspace_id=None,
        )
        return job_to_dict(job)
    except BaseException:
        # 驗證/寫檔/超限/建 job 任一失敗 → 清除本次 UUID 目錄（只刪 imports root 下的本次目錄）。
        await run_in_threadpool(remove_import_dir, target_dir)
        raise
