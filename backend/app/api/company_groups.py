from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.repositories import company_group_repository as repo

router = APIRouter(tags=["company-groups"])


class GroupMemberRequest(BaseModel):
    """使用者手動指定要歸入集團的公司。"""

    company_code: str | None = Field(default=None, max_length=128)
    company_display_name: str = Field(min_length=1, max_length=255)


class GroupCreateRequest(BaseModel):
    """建立 confirmed 集團的 request body。"""

    group_name: str = Field(min_length=1, max_length=255)
    members: list[GroupMemberRequest] = Field(default_factory=list)


class GroupRenameRequest(BaseModel):
    """重新命名集團。"""

    group_name: str = Field(min_length=1, max_length=255)


class SuggestionIngestRequest(BaseModel):
    """CLI/AI 建議匯入；後端會強制轉成 suggested。"""

    suggestions: list[dict[str, Any]] = Field(default_factory=list)


class SuggestionConfirmRequest(BaseModel):
    """確認 AI 建議時可一併修訂父集團名稱。"""

    group_name: str | None = Field(default=None, max_length=255)


@router.get("/company-groups")
def list_company_groups() -> dict[str, Any]:
    """列出集團治理清單。"""
    return {"items": repo.list_company_groups()}


@router.post("/company-groups")
def create_company_group(body: GroupCreateRequest) -> dict[str, Any]:
    """由使用者建立 confirmed 集團。"""
    try:
        return repo.create_manual_group(
            body.group_name,
            [member.model_dump() for member in body.members],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/company-groups/{group_id}")
def rename_company_group(group_id: int, body: GroupRenameRequest) -> dict[str, Any]:
    """重新命名集團。"""
    try:
        return repo.rename_group(group_id, body.group_name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/company-groups/{group_id}")
def delete_company_group(group_id: int) -> dict[str, Any]:
    """解散集團 mapping，不動公司正規化或專利資料。"""
    try:
        return repo.delete_group(group_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/company-groups/{group_id}/members")
def add_company_group_member(group_id: int, body: GroupMemberRequest) -> dict[str, Any]:
    """新增 confirmed 集團成員。"""
    try:
        return repo.add_group_member(
            group_id,
            company_code=body.company_code,
            company_display_name=body.company_display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/company-groups/{group_id}/members/{member_id}")
def remove_company_group_member(group_id: int, member_id: int) -> dict[str, Any]:
    """移除集團成員。"""
    return repo.remove_group_member(group_id, member_id)


@router.post("/company-groups/suggestions")
def ingest_company_group_suggestions(body: SuggestionIngestRequest) -> dict[str, Any]:
    """接收 CLI/AI review-only 建議。"""
    try:
        return repo.ingest_cli_suggestions(body.suggestions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/company-groups/suggestions/{member_id}/confirm")
def confirm_company_group_suggestion(
    member_id: int,
    body: SuggestionConfirmRequest | None = None,
) -> dict[str, Any]:
    """人工確認單筆 CLI/AI 建議。"""
    try:
        if body is not None and body.group_name is not None and not body.group_name.strip():
            raise ValueError("group_name is required")
        return repo.set_suggestion_decision(
            member_id,
            "confirmed",
            group_name=body.group_name if body is not None else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/company-groups/suggestions/{member_id}/reject")
def reject_company_group_suggestion(member_id: int) -> dict[str, Any]:
    """人工拒絕單筆 CLI/AI 建議。"""
    try:
        return repo.set_suggestion_decision(member_id, "rejected")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/company-groups/suggestions/{member_id}/undo-confirm")
def undo_company_group_suggestion_confirmation(member_id: int) -> dict[str, Any]:
    """把已確認的 AI 建議退回待審並保留證據。"""
    try:
        return repo.undo_suggestion_confirmation(member_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
