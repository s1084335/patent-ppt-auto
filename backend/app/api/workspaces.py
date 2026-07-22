"""Workspace 相關 API：查詢（list/detail/patents）、建立與組合建立（compose）。

GET  /api/v1/workspaces                 ：分頁列出 workspace，可選 status filter。
GET  /api/v1/workspaces/{id}            ：單一 workspace 詳情，含直接組合來源。
GET  /api/v1/workspaces/{id}/patents    ：分頁列出 workspace 專利成員，可選 keyword。
POST /api/v1/workspaces                 ：以明確專利集合建立一般 workspace。
POST /api/v1/workspaces/compose         ：由多個既有 workspace 建立組合 workspace（聯集去重）。

查詢為唯讀，SQL 集中於 app_layer.workspace_queries；建立於 app_layer.workspace_create、
compose 於 app_layer.workspace_compose（皆單一 transaction）。本層只做請求驗證、呼叫
service 與 HTTP 錯誤碼對應，不把 SQL 寫進 router。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.app_layer import workspace_compose, workspace_create, workspace_queries


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
