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
    is_global_workspace,
    keep_patents,
    pending_reviews,
    restore_patents,
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

    ⚠ 全庫擋下（400）：排除是 workspace 級，全庫是總覽本就該全收——對 A 不相干的專利
    對全庫可能屬另一技術領域、是相干的（irrelevant-patent-filter-spec 第 62-64 行）。
    全庫的分析成員本就不扣除，對它跑篩選只會白燒 CLI 額度、堆無用的待複核項。
    前端已隱藏入口，這裡是擋直接打 API 的後端護欄。
    """
    if is_global_workspace(workspace_id):
        raise HTTPException(
            status_code=400,
            detail="全庫 workspace 不做不相干篩選：排除是 workspace 級，全庫照收所有專利",
        )
    job_id = create_job(
        "ai:irrelevant_filter",
        {"workspace_id": workspace_id},
        workspace_id=workspace_id,
    )
    return {"workspace_id": workspace_id, "job_id": job_id}


@router.post("/workspaces/{workspace_id}/patent-notes")
def trigger_patent_notes(workspace_id: int) -> dict[str, Any]:
    """手動觸發 AI 文獻備註（2026-07-27 定案：改手動，原匯入後自動觸發已撤回）。

    ⚠ **只補空值、不覆蓋**（`skip_existing=True`）：已有備註的專利會被 runner 的查詢
    條件排除，第二次按不會重寫——既有備註可能已經人工確認過，AI 不得蓋掉。

    為何需要這個入口（實機）：AI 第一次跑失敗後，**同一檔案再匯入會被去重擋掉**
    （inserted 0），那批專利再也不會被自動觸發，缺備註的專利沒有任何補救管道。
    全庫亦可觸發（runner 對 workspace_id 無全庫限制，備註是專利級資料）。
    """
    job_id = create_job(
        "ai:patent_note",
        {"workspace_id": workspace_id, "skip_existing": True},
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


@router.post("/workspaces/{workspace_id}/excluded-patents/restore")
def restore_excluded_patents(
    workspace_id: int, request: ExclusionReviewDecisionRequest
) -> dict[str, Any]:
    """把已排除的專利放回原主題（2026-07-27 使用者要求：預防後悔）。

    依排除當下存下的主題快照（0037 `restored_topic_key`）還原各通道的 assignment，
    含原 distance_to_centroid（不重算、不重跑分群），並移出排除清單。
    放回後該筆重新計入分群與報表統計。

    原 run 已被刪除（例如事後重跑分群）時該通道還原不了，但仍會移出排除清單——
    使用者要它回來，不能因為還原不了就繼續關著；該筆可由下次分群重新指派。
    """
    import psycopg

    with psycopg.connect(**get_connection_kwargs()) as conn:
        count = restore_patents(workspace_id, request.patent_ids, conn=conn)
        conn.commit()
    return {"workspace_id": workspace_id, "restored_count": count}


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
# 技術文獻
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


# ══════════ 初階篩選：負面關鍵字（PRE-001，切片 A）══════════
# ⚠ 路徑一律帶 workspace_id：關鍵字以 workspace 為單位，沒有跨庫端點。
# ⚠ 全庫 workspace 由 prefilter.keywords 內部拒絕（沿用 CLU-007），
#   本層只負責把該例外轉成 400——不在這裡再判一次 is_global。


class NegativeKeywordCreateRequest(BaseModel):
    """建立負面關鍵字：只收原始詞。

    ⚠ 刻意**不收** match_terms 與 terms_confirmed：比對詞由 AI 轉換或使用者
    後續填入，且一律以未確認狀態起始（PRE-002）。開放這裡帶入等於讓呼叫端
    可以繞過確認流程。
    """

    original_term: str = Field(min_length=1, max_length=200)


class NegativeKeywordUpdateRequest(BaseModel):
    """更新比對詞、確認狀態或啟用旗標；未給的欄位不動。"""

    match_terms: list[str] | None = None
    terms_confirmed: bool | None = None
    enabled: bool | None = None


@router.get("/workspaces/{workspace_id}/negative-keywords")
async def list_negative_keywords(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """列出該 workspace 的負面關鍵字（含停用者，供治理介面重新啟用）。"""
    from backend.app.prefilter import keywords as kw

    items = await run_in_threadpool(kw.list_keywords, workspace_id)
    return {"workspace_id": workspace_id, "items": items}


@router.post("/workspaces/{workspace_id}/negative-keywords")
async def create_negative_keyword(
    request: NegativeKeywordCreateRequest,
    workspace_id: int = Path(ge=1),
) -> dict[str, Any]:
    """建立一筆負面關鍵字（比對詞留空、未確認）。"""
    from backend.app.prefilter import keywords as kw

    try:
        row = await run_in_threadpool(
            kw.create_keyword, workspace_id, request.original_term)
    except kw.PrefilterScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"workspace_id": workspace_id, "keyword": row}


@router.patch("/workspaces/{workspace_id}/negative-keywords/{keyword_id}")
async def update_negative_keyword(
    request: NegativeKeywordUpdateRequest,
    workspace_id: int = Path(ge=1),
    keyword_id: int = Path(ge=1),
) -> dict[str, Any]:
    """更新比對詞／確認狀態／啟用旗標。

    ⚠ 先驗該筆確實屬於這個 workspace 才更新——沒驗的話，知道 keyword_id
    就能改別的 workspace 的關鍵字（路徑帶了 workspace_id 卻不用，等於裝飾）。
    """
    from backend.app.prefilter import keywords as kw

    owned = await run_in_threadpool(kw.list_keywords, workspace_id)
    if keyword_id not in {int(r["keyword_id"]) for r in owned}:
        raise HTTPException(
            status_code=404,
            detail=f"keyword {keyword_id} not found in workspace {workspace_id}")
    try:
        row = await run_in_threadpool(
            lambda: kw.update_keyword(
                keyword_id,
                match_terms=request.match_terms,
                terms_confirmed=request.terms_confirmed,
                enabled=request.enabled,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"workspace_id": workspace_id, "keyword": row}


@router.post("/workspaces/{workspace_id}/negative-keywords/{keyword_id}/expand")
async def trigger_keyword_expand(
    workspace_id: int = Path(ge=1),
    keyword_id: int = Path(ge=1),
) -> dict[str, Any]:
    """派工把負面關鍵字轉成英文比對詞（PRE-002）。

    ⚠ 先驗歸屬：路徑帶了 workspace_id 不等於守住了——不驗的話，
    知道 keyword_id 就能替別的 workspace 派工。

    ⚠ 產出一律為未確認草稿，使用者確認才生效；轉換失敗也不阻斷——
    使用者仍可自行輸入英文比對詞（那條路徑不經過本端點）。
    """
    from backend.app.prefilter import keywords as kw

    owned = await run_in_threadpool(kw.list_keywords, workspace_id)
    row = next((r for r in owned if r["keyword_id"] == keyword_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="關鍵字不存在或不屬於此 workspace")

    # ⚠ create_job 回的是 ProcessingJob 物件不是 id。本檔既有的
    # trigger_irrelevant_filter／trigger_patent_notes 直接把物件放在 `job_id`
    # 鍵下（前端只看 resp.ok，所以一直沒人發現）。新端點回真正的整數，
    # 讓鍵名與內容相符——追進度要拿它去打 /tasks。
    job = create_job(
        "ai:keyword_expand",
        {"keyword_id": keyword_id, "original_term": row["original_term"]},
        workspace_id=workspace_id,
    )
    return {"workspace_id": workspace_id, "keyword_id": keyword_id,
            "job_id": job.job_id}


@router.post("/workspaces/{workspace_id}/prefilter/review")
async def trigger_prefilter_review(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """派工讓 AI 對命中專利建議留或剔（PRE-008）。

    🔴 **沒填範圍描述就擋在這裡**（400），不讓 job 建出來再失敗：
    使用者按下按鈕後要**立刻**知道該去填什麼，而不是等幾分鐘看到一個
    失敗的任務卡——後者不但慢，還會被誤讀成「AI 壞了」。

    ⚠ 全庫擋下：初階篩選是 workspace 級，全庫是總覽本就該全收。
    """
    from backend.app.prefilter import scope

    if await run_in_threadpool(is_global_workspace, workspace_id):
        raise HTTPException(
            status_code=400,
            detail="全庫 workspace 不做初階篩選：全庫是總覽，本就該全收")

    text = await run_in_threadpool(scope.get_scope_description, workspace_id)
    if not text:
        raise HTTPException(
            status_code=400,
            detail="尚未填寫這批專利的範圍描述。AI 需要它才能判斷命中專利"
                   "是否屬於本批範圍；請先於初階篩選頁填寫一句範圍描述。")

    job = create_job(
        "ai:prefilter_review",
        {"workspace_id": workspace_id},
        workspace_id=workspace_id,
    )
    return {"workspace_id": workspace_id, "job_id": job.job_id}


class PrefilterScopeRequest(BaseModel):
    """整批專利的範圍描述（PRE-008 的判讀依據）。"""

    scope_description: str = ""


@router.get("/workspaces/{workspace_id}/prefilter/scope")
async def get_prefilter_scope(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """取得範圍描述；未設定回空字串。"""
    from backend.app.prefilter import scope

    text = await run_in_threadpool(scope.get_scope_description, workspace_id)
    return {
        "workspace_id": workspace_id,
        "scope_description": text,
        "max_length": scope.MAX_SCOPE_LENGTH,
    }


@router.put("/workspaces/{workspace_id}/prefilter/scope")
async def put_prefilter_scope(
    request: PrefilterScopeRequest,
    workspace_id: int = Path(ge=1),
) -> dict[str, Any]:
    """寫入範圍描述。空字串＝清除。

    ⚠ 兩種 ValueError 要分開回：超長是**輸入格式問題**（422），
    全庫 workspace 是**對象不適用**（400）。混成同一碼的話，
    前端無法決定該提示「縮短一點」還是「這個 workspace 不做初階篩選」。
    """
    from backend.app.prefilter import scope

    if len(request.scope_description.strip()) > scope.MAX_SCOPE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"範圍描述最長 {scope.MAX_SCOPE_LENGTH} 字")
    try:
        text = await run_in_threadpool(
            scope.set_scope_description, workspace_id, request.scope_description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"workspace_id": workspace_id, "scope_description": text}


@router.get("/workspaces/{workspace_id}/prefilter/preview")
async def preview_prefilter(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """逐關鍵字的命中件數預覽（PRE-004）。

    🔴 零命中回 0 而**不省略該列**——「算過了，結果是 0」與「沒算」必須分得開。
    ⚠ 只算已確認且啟用的關鍵字；未確認者本來就不該產生任何命中（PRE-002）。
    """
    from backend.app.prefilter import matching

    items = await run_in_threadpool(matching.preview_counts, workspace_id)
    return {"workspace_id": workspace_id, "items": items}


@router.get("/workspaces/{workspace_id}/prefilter/summary")
async def prefilter_summary(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """初階篩選入口要顯示的待辦數（WSP-013）。

    ⚠ 待辦數由**後端算**：前端自數會變成第二份計數邏輯，兩份必然漂移
    ——本專案已反覆踩過。
    """
    from backend.app.prefilter import decisions
    from backend.app.prefilter import keywords as kw

    def _collect() -> dict[str, int]:
        rows = kw.list_keywords(workspace_id)
        return {
            "keyword_count": len(rows),
            "unconfirmed_count": sum(1 for r in rows if not r["terms_confirmed"]),
            # 🔴 與 `/prefilter/reviews` **同一個口徑**（只算 source='prefilter'）。
            # ⚠ 用 pending_reviews 會把 AI 線的待複核也算進來，於是出現
            # 「徽章說 5 筆、點進去只有 3 筆」——使用者會以為系統壞了。
            "pending_count": len(decisions.pending_prefilter_reviews(workspace_id)),
            "archived_count": len(excluded_patent_rows(workspace_id)),
        }

    counts = await run_in_threadpool(_collect)
    # 入口徽章顯示的單一數字＝待確認比對詞 ＋ 待裁決，兩者都是「等使用者動作」。
    counts["todo_count"] = counts["unconfirmed_count"] + counts["pending_count"]
    return {"workspace_id": workspace_id, **counts}


class PrefilterTermCountsRequest(BaseModel):
    """要試算的比對詞（通常是尚未確認的 AI 建議詞）。"""

    terms: list[str] = []


@router.post("/workspaces/{workspace_id}/prefilter/term-counts")
async def prefilter_term_counts(
    request: PrefilterTermCountsRequest,
    workspace_id: int = Path(ge=1),
) -> dict[str, Any]:
    """試算比對詞的命中件數與**實際命中的詞形**（確認畫面用）。

    ## 🔴 為什麼不共用 `/prefilter/preview`

    那支只算**已確認**的關鍵字（PRE-002：未確認者不得產生任何命中）。
    確認畫面要看的正好是還沒確認的那些。

    ## 為什麼要回詞形

    AI 給的是**詞幹**（`machin`、`mechaniz`），因為比對採前綴詞界——
    實測 `mow` 用完整詞界只中 11 件、用前綴中 177 件。

    ⚠ 但同一機制讓 `engine` 也命中 `engineering`。畫面上只給幾個看起來像
    拼錯的字，使用者沒有依據判斷哪個太寬，就只剩「全部照按」或
    「全部不敢按」兩條路。回詞形是把這件事變成看得到的。

    ⚠ 以 POST 而非 GET：比對詞是一組不定長字串，塞 query string 會遇到
    長度與跳脫問題（使用者輸入 `c++`／`a|b` 是常態）。
    """
    from backend.app.clustering.exclusions import display_member_patent_ids
    from backend.app.prefilter import matching

    def _collect() -> list[dict[str, Any]]:
        # 🔴 只算本 workspace 的成員：掃全庫的話畫面數字與實際套用結果不同，
        # 使用者會照著一個永遠對不上的數字做決定。
        member_ids = display_member_patent_ids(workspace_id)
        return matching.term_hit_summary(request.terms, patent_ids=member_ids)

    items = await run_in_threadpool(_collect)
    return {"workspace_id": workspace_id, "items": items}


@router.get("/workspaces/{workspace_id}/prefilter/reviews")
async def list_prefilter_reviews(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """初階篩選的待裁決清單：命中原文 ＋ AI 建議（PRE-005／PRE-008）。

    ⚠ 只列 `source='prefilter'`——AI 線的待複核沒有命中關鍵字，混進來會是
    一列「沒有命中原因」的東西。它有自己的呈現處（分類頁）。

    ⚠ `scope_verdict` 為 `null` 代表**尚未產生建議**，`'no_basis'` 代表
    跑過但三個判讀欄位皆空。🔴 前端必須把兩者顯示成不同的東西，
    不得都留白——空白會被讀成「沒問題」。
    """
    from backend.app.prefilter import decisions

    items = await run_in_threadpool(
        decisions.pending_prefilter_reviews, workspace_id)
    return {"workspace_id": workspace_id, "items": items}


@router.post("/workspaces/{workspace_id}/prefilter/apply")
async def apply_prefilter_endpoint(workspace_id: int = Path(ge=1)) -> dict[str, Any]:
    """執行比對，把命中寫成待裁決項（PRE-005）。

    🔴 只寫 `pending`，不直接排除——使用者裁決才算數。
    🔴 跳過已保留（`kept`）與已封存（`excluded`）者（CLU-017）。
    """
    from backend.app.prefilter import decisions

    count = await run_in_threadpool(decisions.apply_prefilter, workspace_id)
    return {"workspace_id": workspace_id, "matched_count": count}


@router.delete("/workspaces/{workspace_id}/negative-keywords/{keyword_id}")
async def delete_negative_keyword(
    workspace_id: int = Path(ge=1),
    keyword_id: int = Path(ge=1),
) -> dict[str, Any]:
    """刪除一筆關鍵字。⚠ 停用請用 PATCH `enabled=false`，不要用刪除代替。"""
    from backend.app.prefilter import keywords as kw

    owned = await run_in_threadpool(kw.list_keywords, workspace_id)
    if keyword_id not in {int(r["keyword_id"]) for r in owned}:
        raise HTTPException(
            status_code=404,
            detail=f"keyword {keyword_id} not found in workspace {workspace_id}")
    await run_in_threadpool(kw.delete_keyword, keyword_id)
    return {"deleted": True, "keyword_id": keyword_id, "workspace_id": workspace_id}
