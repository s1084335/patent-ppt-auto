"""Workspace 相關 API：組合建立（compose）。

POST /api/v1/workspaces/compose：由多個既有 workspace 建立組合 workspace（聯集去重）。
這是 Backend 業務能力，不由前端自行拉多份專利清單合併。實際寫入與單一 transaction
在 app_layer.workspace_compose；本層只做請求驗證與錯誤碼對應。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.app_layer import workspace_compose


router = APIRouter(tags=["workspaces"])


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
