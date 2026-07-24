"""市場資料 PDF 上傳 API（/api/v1/market-documents）。

市場 PDF 落**檔案系統**（MARKET_DOC_ROOT，NAS 佔位）——讀取者是本機 Companion 驅動的
CLI，非 Railway 容器；DB 只存 metadata（與存 bytea in DB 的 workspace_documents 不同）。

沿用 imports.py／workspaces.py 的串流上傳模式：先在 MARKET_DOC_ROOT 產唯一落地檔名 →
內容逐塊寫檔並累計 hash／byte_size → 記 metadata。驗證／寫檔／記錄任一失敗都刪除本次
落地檔與 metadata 列，不留孤兒。

⚠ PDF 通道物理隔離：上傳走 DOCUMENT_SUFFIXES（只 .pdf），專利匯入端點仍用
WEB_IMPORT_SUFFIXES（不含 .pdf），PDF 進不了 WIPS parser。
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from backend.app import settings
from backend.app.importers.import_paths import DOCUMENT_SUFFIXES, validate_web_filename
from backend.app.market import market_doc_store
from backend.app.settings import get_market_doc_root


router = APIRouter(tags=["market-documents"])


def _stored_path(root: Path, workspace_id: int, safe_name: str) -> tuple[str, Path]:
    """在 MARKET_DOC_ROOT 產唯一落地檔名（不覆蓋既有、不含使用者原檔名路徑成分）。

    落地檔名＝ws{id}-{uuid}{副檔名}：uuid 保證多份／同原檔名不衝突；副檔名沿原檔（已過
    validate_web_filename 白名單）。回傳（相對 root 的 stored_filename, 絕對路徑）。
    """
    suffix = Path(safe_name).suffix.lower()
    stored_filename = f"ws{workspace_id}-{uuid.uuid4().hex}{suffix}"
    return stored_filename, root / stored_filename


def _write_upload_sync(dest: Path, chunks: list[bytes]) -> None:
    """把已收齊的分塊寫入落地檔（阻塞 I/O，走 threadpool）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        for chunk in chunks:
            fh.write(chunk)


@router.post("/market-documents")
async def upload_market_document(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    workspace_id: int = Query(..., ge=1),
) -> dict[str, Any]:
    """串流接收市場 PDF、落 MARKET_DOC_ROOT 檔案系統、記 metadata 進 DB。

    空檔或非 PDF → 422；path traversal → 422；超過 MAX_IMPORT_UPLOAD_BYTES → 413；
    metadata 寫入失敗 → 500 並清落地檔。同一 workspace 可上傳多份（不覆蓋既有）。
    """
    max_bytes = settings.MAX_IMPORT_UPLOAD_BYTES

    # Content-Length 若存在且超限，提早 413（尚未落檔）；缺漏或偽造則靠串流累計強制限制。
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_int = int(declared)
        except ValueError:
            declared_int = None
        if declared_int is not None and declared_int > max_bytes:
            raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")

    # 檔名驗證（PDF 白名單、拒 traversal）先於任何落地動作。
    try:
        safe_name = validate_web_filename(filename, DOCUMENT_SUFFIXES)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    root = get_market_doc_root()
    stored_filename, dest = _stored_path(root, workspace_id, safe_name)

    # 先在記憶體串流累計並算 hash／大小；累計超限即 413（尚未落檔）。分塊寫檔一次完成，
    # 讓失敗清理只需刪一個檔（不必處理半寫狀態）。上限 200 MiB 由容量檢查保護。
    hasher = hashlib.sha256()
    total = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")
        hasher.update(chunk)
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=422, detail="empty upload body")

    file_hash = hasher.hexdigest()
    await run_in_threadpool(_write_upload_sync, dest, chunks)

    # metadata 記錄失敗 → 刪除已落地檔，不留孤兒檔。
    store = market_doc_store.MarketDocumentStore()
    try:
        document_id = await run_in_threadpool(
            store.record_document,
            workspace_id,
            original_filename=safe_name,
            stored_filename=stored_filename,
            file_hash=file_hash,
            byte_size=total,
        )
    except Exception as exc:
        await run_in_threadpool(_unlink_quiet, dest)
        raise HTTPException(status_code=500, detail="failed to record market document") from exc

    return {
        "document_id": document_id,
        "workspace_id": workspace_id,
        "original_filename": safe_name,
        "stored_filename": stored_filename,
        "byte_size": total,
        "file_hash": file_hash,
    }


@router.get("/market-documents")
async def list_market_documents(workspace_id: int = Query(..., ge=1)) -> dict[str, Any]:
    """列出某 workspace 的市場 PDF metadata（新到舊；不回內容，內容在檔案系統）。"""
    store = market_doc_store.MarketDocumentStore()
    documents = await run_in_threadpool(store.list_documents, workspace_id)
    return {"documents": documents, "total": len(documents)}


class AcceptSummaryRequest(BaseModel):
    """確認某市場摘要（逐筆確認落款）；只需 summary_id。"""

    summary_id: int


@router.get("/market-summaries/current")
async def get_current_market_summary(
    workspace_id: int = Query(..., ge=1),
    accepted_only: bool = Query(default=False),
) -> dict[str, Any]:
    """取某 workspace 的現行版市場摘要，供前端顯示。

    無摘要回 {"summary": null}——前端據此隱藏市場區塊（不顯示空表、不留佔位）。
    - accepted_only=False（預設）：回「現行版」（可能尚未 accept），供逐筆確認前端顯示草稿。
    - accepted_only=True：回「已確認現行版」（未確認草稿回 null）——報表／並排區只讀此結果，
      未確認草稿實體上進不了報表（實體隔離）。
    """
    store = market_doc_store.MarketDocSummaryStore()
    getter = store.get_accepted_current if accepted_only else store.get_current
    summary = await run_in_threadpool(getter, workspace_id)
    return {"summary": summary}


@router.post("/market-summaries/accept")
async def accept_market_summary(body: AcceptSummaryRequest) -> dict[str, Any]:
    """確認落款某市場摘要（accepted_at）；回 {"accepted": bool}。

    accepted=False 表示該摘要已確認過（不重複落款）或不存在——確認為冪等，重按不改時間。
    確認後報表側 get_accepted_current 才拿得到（未確認草稿實體上進不了報表）。
    """
    store = market_doc_store.MarketDocSummaryStore()
    accepted = await run_in_threadpool(store.accept, body.summary_id)
    return {"accepted": accepted}


def _unlink_quiet(path: Path) -> None:
    """刪除落地檔，忽略不存在（清理路徑用，不因清理失敗再拋例外）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
