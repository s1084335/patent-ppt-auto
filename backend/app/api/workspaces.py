"""Workspace 相關 API：查詢（list/detail/patents）、建立與組合建立（compose）。

GET  /api/v1/workspaces                 ：分頁列出 workspace，可選 status filter。
GET  /api/v1/workspaces/{id}            ：單一 workspace 詳情，含直接組合來源。
GET  /api/v1/workspaces/{id}/patents    ：分頁列出 workspace 專利成員，可選 keyword。
POST /api/v1/workspaces                 ：以明確專利集合建立一般 workspace。
POST /api/v1/workspaces/compose         ：由多個既有 workspace 建立組合 workspace（聯集去重）。
POST   /api/v1/workspaces/{id}/documents              ：上傳技術文獻 PDF（串流分塊存 DB）。
GET    /api/v1/workspaces/{id}/documents              ：列出文獻 metadata（**不回 content**）。
GET    /api/v1/workspaces/{id}/documents/{doc}/content：單筆取回文獻原始內容。
DELETE /api/v1/workspaces/{id}/documents/{doc}        ：刪除文獻。

查詢為唯讀，SQL 集中於 app_layer.workspace_queries；建立於 app_layer.workspace_create、
compose 於 app_layer.workspace_compose（皆單一 transaction）。本層只做請求驗證、呼叫
service 與 HTTP 錯誤碼對應，不把 SQL 寫進 router。
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.app import settings
from backend.app.app_layer import workspace_compose, workspace_create, workspace_queries
from backend.app.clustering.exclusions import (
    confirm_exclusions,
    exclude_patents,
    excluded_patent_rows,
    keep_patents,
    pending_reviews,
)
from backend.app.db import workspace_document_store
from backend.app.db.job_repository import create_job
from backend.app.db.connection import get_connection_kwargs
from backend.app.importers.import_paths import DOCUMENT_SUFFIXES, validate_web_filename


router = APIRouter(tags=["workspaces"])


# 允許的 workspace 狀態（與 workspaces_status_check 一致）；非法值由 FastAPI 擋成 422。
WorkspaceStatus = Literal["active", "archived", "disabled"]

# 匯入批次用途標籤（2026-07-22）：專利總覽可依此過濾/顯示；非法值由 FastAPI 擋成 422。
WorkspacePurpose = Literal["general", "case_comparison"]


@router.get("/workspaces")
def list_workspaces(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: Optional[WorkspaceStatus] = Query(default=None),
    purpose: Optional[WorkspacePurpose] = Query(default=None),
) -> dict[str, Any]:
    """分頁列出 workspace，含 purpose、patent_count 與 is_composed。

    回傳 {items, total, limit, offset}；排序固定 workspace_id DESC。limit/offset/status/purpose
    由 FastAPI 驗證（越界或非法值回 422）。purpose 供專利總覽依用途（general／case_comparison）
    過濾；未給則不過濾（general 過濾也涵蓋無 purpose 鍵的舊 workspace）。
    """
    return workspace_queries.list_workspaces(
        limit=limit, offset=offset, status=status, purpose=purpose)


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: int) -> dict[str, Any]:
    """取單一 workspace 詳情，含直接組合來源；workspace 不存在回 404。"""
    detail = workspace_queries.get_workspace_detail(workspace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    return detail


@router.get("/workspaces/{workspace_id}/patents")
def list_workspace_patents(
    workspace_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    keyword: Optional[str] = Query(default=None, max_length=200),
) -> dict[str, Any]:
    """分頁列出 workspace 專利成員（可選 keyword）；workspace 不存在回 404。

    回傳 {items, total, limit, offset}；items 為前端選取所需的既有欄位
    （patent_id／patent_number／title／country_code／applicant_display_name）與完整度旗標
    has_technical_text／has_effect_text。keyword 同時搜尋 patent_number／title／
    applicant_display_name。limit/offset 由 FastAPI 驗證。
    """
    result = workspace_queries.list_workspace_patents(
        workspace_id=workspace_id, limit=limit, offset=offset, keyword=keyword
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    return result


class CreateWorkspaceRequest(BaseModel):
    """建立一般 workspace 請求：以明確專利集合建立，不設數量上限。"""

    workspace_name: str = Field(min_length=1, max_length=120)
    patent_ids: list[int] = Field(min_length=1)
    created_by: str = Field(default="api-user", min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


@router.post("/workspaces")
def create_workspace(request: CreateWorkspaceRequest) -> dict[str, Any]:
    """以明確專利集合建立一般 workspace（單一 transaction，聯集去重）。

    回傳 {workspace_id, workspace_name, patent_count}。名稱衝突 409；去重後專利集為空、
    或有 patent_id 不存在等輸入問題 422；任一步失敗整筆 rollback，不留半成品。
    """
    try:
        return workspace_create.create_workspace(
            workspace_name=request.workspace_name,
            patent_ids=request.patent_ids,
            created_by=request.created_by,
            description=request.description,
        )
    except workspace_create.WorkspaceNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspace_create.WorkspaceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class ExcludePatentsRequest(BaseModel):
    """使用者標「不相干」剔除單/多筆專利（#E 2026-07-26）：不重跑分群。"""

    patent_ids: list[int] = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/workspaces/{workspace_id}/exclude-patents")
def exclude_workspace_patents(
    workspace_id: int, request: ExcludePatentsRequest
) -> dict[str, Any]:
    """使用者剔除（標不相干）該 workspace 的專利：呼叫既有 exclude_patents 引擎。

    引擎語意（不重跑分群）：回寫排除表（可追溯、可反悔）＋移除該筆 topic_assignments，
    **不動 model artifact、不重算距離**（見 clustering.exclusions.exclude_patents）。
    同一 reason 套用於這批全部 patent_ids（前端一次針對一組理由剔除）。回實際剔除筆數。
    """
    import psycopg

    entries = [(pid, request.reason) for pid in request.patent_ids]
    with psycopg.connect(**get_connection_kwargs()) as conn:
        count = exclude_patents(workspace_id, entries, conn=conn)
        conn.commit()
    return {"workspace_id": workspace_id, "excluded_count": count}


@router.post("/workspaces/{workspace_id}/irrelevant-filter")
def trigger_irrelevant_filter(workspace_id: int) -> dict[str, Any]:
    """手動觸發 AI 不相干篩選（2026-07-27 定案：改手動，原自動接續已撤回）。

    建一筆 ai:irrelevant_filter job 交由 Companion 領走；runner 取每主題 c-TF-IDF 最低
    N 筆逐筆判讀，結果落 status='pending' 待使用者以「保留／確定」裁決
    （見 clustering.exclusions.store_ai_verdicts）。回 job_id 供前端追蹤進度。
    """
    job_id = create_job(
        "ai:irrelevant_filter",
        {"workspace_id": workspace_id},
        workspace_id=workspace_id,
    )
    return {"workspace_id": workspace_id, "job_id": job_id}


@router.get("/workspaces/{workspace_id}/exclusion-reviews")
def list_exclusion_reviews(workspace_id: int) -> dict[str, Any]:
    """列出待複核清單（AI 判讀為不相干、尚未裁決者）。

    ⚠ 只回 status='pending'：這些專利仍留在原主題、仍參與分析，等使用者裁決。
    已確定排除者不在此清單（它們已在「不相干」桶）。
    """
    items = pending_reviews(workspace_id)
    return {"workspace_id": workspace_id, "items": items}


@router.get("/workspaces/{workspace_id}/excluded-patents")
def list_excluded_patents(workspace_id: int) -> dict[str, Any]:
    """列出「不相干」桶內容（已確定排除者）。

    人工剔除與 AI 判讀經使用者確定者都在此（2026-07-27 定案：兩種來源同一個桶）。
    帶 source 供前端區分來源。待複核（pending）不在此清單，走 exclusion-reviews。
    """
    items = excluded_patent_rows(workspace_id)
    return {"workspace_id": workspace_id, "items": items}


class ExclusionReviewDecisionRequest(BaseModel):
    """複核裁決請求：一次可裁決多筆（前端支援批次勾選）。"""

    patent_ids: list[int] = Field(min_length=1)


@router.post("/workspaces/{workspace_id}/exclusion-reviews/keep")
def keep_exclusion_reviews(
    workspace_id: int, request: ExclusionReviewDecisionRequest
) -> dict[str, Any]:
    """使用者按「保留」：該筆留在原主題，從待複核清單移除（刪列）。

    topic_assignments 不動——pending 階段從未移除指派，保留即維持現狀。
    """
    import psycopg

    with psycopg.connect(**get_connection_kwargs()) as conn:
        count = keep_patents(workspace_id, request.patent_ids, conn=conn)
        conn.commit()
    return {"workspace_id": workspace_id, "kept_count": count}


@router.post("/workspaces/{workspace_id}/exclusion-reviews/confirm")
def confirm_exclusion_reviews(
    workspace_id: int, request: ExclusionReviewDecisionRequest
) -> dict[str, Any]:
    """使用者按「確定」：歸類到「不相干」（pending → excluded）並移除 topic_assignments。

    確定後該筆不再參與分群與統計（analysis_member_patent_ids 扣除），
    與人工剔除（exclude-patents）同在「不相干」桶呈現。不重跑分群。
    """
    import psycopg

    with psycopg.connect(**get_connection_kwargs()) as conn:
        count = confirm_exclusions(workspace_id, request.patent_ids, conn=conn)
        conn.commit()
    return {"workspace_id": workspace_id, "confirmed_count": count}


class ComposeRequest(BaseModel):
    """組合建立請求：至少兩個來源 workspace，數量不設業務上限。"""

    workspace_name: str = Field(min_length=1, max_length=120)
    source_workspace_ids: list[int] = Field(min_length=2)
    created_by: str = Field(default="api-user", min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


@router.post("/workspaces/compose")
def compose(request: ComposeRequest) -> dict[str, Any]:
    """建立組合 workspace：聯集去重、來源不動、不繼承 topics、不自動分群。

    回傳新 workspace_id、各來源件數、重複件數與聯集件數。來源不存在 404、
    非 active 409、去重後不足兩個 422；任一步失敗整筆 rollback。
    """
    try:
        return workspace_compose.compose_workspaces(
            workspace_name=request.workspace_name,
            source_workspace_ids=request.source_workspace_ids,
            created_by=request.created_by,
            description=request.description,
        )
    except workspace_compose.SourceWorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except workspace_compose.SourceWorkspaceNotActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspace_compose.WorkspaceNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspace_compose.ComposeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 技術文獻（market research 線）
#
# 使用者上傳的產業／技術 PDF，供 CLI 推導產品定義（scope）與市場證據。內容長期保存在
# app_layer.workspace_documents（0027），與用完即刪的 import_blobs 物理分離。
#
# ⚠ PDF 通道**只開在這裡**：上傳走 DOCUMENT_SUFFIXES，專利匯入端點仍用
# WEB_IMPORT_SUFFIXES（不含 .pdf），PDF 進不了 WIPS parser。
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/documents")
async def upload_workspace_document(
    request: Request,
    workspace_id: int = Path(ge=1),
    filename: str = Query(..., min_length=1, max_length=255),
) -> dict[str, Any]:
    """串流接收技術文獻 PDF、分塊存進 app_layer.workspace_documents。

    沿用 imports.py 的串流上傳模式：先建列取 document_id → 內容逐塊 append → 落款 hash；
    驗證／寫入／超限任一失敗都刪除本次列，不留孤兒內容。同一 workspace 可上傳多份
    （不覆蓋既有）。空檔或非 PDF → 422；超過 MAX_IMPORT_UPLOAD_BYTES → 413。
    """
    max_bytes = settings.MAX_IMPORT_UPLOAD_BYTES

    # Content-Length 若存在且超限，提早 413（尚未建列）；缺漏或偽造則靠串流累計強制限制。
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

    # 先建空列取得 document_id，內容再逐塊 append；DB 寫入屬阻塞 I/O，一律走 threadpool。
    document_id = await run_in_threadpool(
        workspace_document_store.create_document, workspace_id, safe_name
    )
    try:
        hasher = hashlib.sha256()
        total = 0
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"upload exceeds {max_bytes} bytes")
            hasher.update(chunk)
            await run_in_threadpool(workspace_document_store.append_chunk, document_id, chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="empty upload body")
        file_hash = hasher.hexdigest()
        await run_in_threadpool(
            workspace_document_store.finalize_document, document_id, file_hash=file_hash
        )
        return {
            "document_id": document_id,
            "workspace_id": workspace_id,
            "original_filename": safe_name,
            "byte_size": total,
            "file_hash": file_hash,
        }
    except BaseException:
        # 任一失敗 → 刪除本次列（id 為本次呼叫產生，不會誤刪他人資料），不留孤兒內容。
        await run_in_threadpool(workspace_document_store.delete_document, document_id)
        raise


@router.get("/workspaces/{workspace_id}/documents")
async def list_workspace_documents(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """列出 workspace 的技術文獻 metadata（新到舊）。

    ⚠ **不回 content**：護欄實作在 store 層 SQL 的選欄（明確列欄、大小以 length(content)
    在 DB 端算），本端點只是把該結果原樣回傳，不存在「撈全欄再篩」的路徑。
    """
    documents = await run_in_threadpool(workspace_document_store.list_documents, workspace_id)
    return {"documents": documents, "total": len(documents)}


@router.get("/workspaces/{workspace_id}/documents/{document_id}/content")
async def get_workspace_document_content(
    workspace_id: int = Path(ge=1),
    document_id: int = Path(ge=1),
):
    """單筆取回文獻原始內容，供 Companion 落本機暫存檔給 CLI 讀；查無 404。

    只在這支端點碰 content——列表端點永遠不需要內容。
    """
    document = await run_in_threadpool(
        workspace_document_store.read_document, workspace_id, document_id
    )
    if document is None:
        raise HTTPException(status_code=404, detail=f"document not found: {document_id}")
    return Response(
        content=document["content"],
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{document["original_filename"]}"'
        },
    )


@router.delete("/workspaces/{workspace_id}/documents/{document_id}")
async def delete_workspace_document(
    workspace_id: int = Path(ge=1),
    document_id: int = Path(ge=1),
) -> dict[str, Any]:
    """刪除指定文獻；查無 404。帶 workspace_id 定位，不允許跨 workspace 刪除。"""
    deleted = await run_in_threadpool(
        workspace_document_store.delete_document, document_id, workspace_id=workspace_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"document not found: {document_id}")
    return {"deleted": True, "document_id": document_id, "workspace_id": workspace_id}
