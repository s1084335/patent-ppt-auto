"""Topic API：正式 HTTP 契約與依賴注入。

六個 endpoints：
1. GET  /workspaces/{workspace_id}/topics?source_field=...
2. GET  /workspaces/{workspace_id}/topics/merge-suggestions?source_field=...
3. POST /workspaces/{workspace_id}/topics/merge
4. GET  /workspaces/{workspace_id}/topics/merge-history?source_field=...
5. POST /workspaces/{workspace_id}/topics/unmerge
6. PATCH /workspaces/{workspace_id}/topics/{topic_key}

不直接 import psycopg、不寫 SQL；透過 TopicRepository Protocol 操作。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator

from backend.app.clustering.sources import get_source_spec
from backend.app.repositories.topic_repository import (
    TopicRepository,
    TopicNotFoundError,
    WorkspaceNotFoundError,
    InvalidTopicOperationError,
    TopicRepositoryUnavailableError,
    ListTopicsResult,
    ListMergeSuggestionsResult,
    MergeQueueResult,
    MergeHistoryItem,
    UnmergeQueueResult,
    RenameResult,
    get_topic_repository,
)

router = APIRouter(tags=["topics"])


# ── Request / Response Schemas ─────────────────────────────────────


class TopicResponse(BaseModel):
    """單一主題回應。"""

    topic_key: str
    label: str
    summary: str
    doc_count: int
    keywords: list[str]
    label_source: str
    display_order: int
    status: str
    merged_into_topic_key: str | None


class TopicsListResponse(BaseModel):
    """列出主題回應。"""

    workspace_id: int
    source_field: str
    run_id: int
    topics: list[TopicResponse]


class MergeSuggestionItem(BaseModel):
    """合併建議項目。"""

    topic_keys: list[str]
    labels: list[str]
    distance: float


class MergeSuggestionsResponse(BaseModel):
    """合併建議回應。"""

    workspace_id: int
    source_field: str
    suggestions: list[MergeSuggestionItem]


class MergeRequest(BaseModel):
    """合併請求。"""

    source_field: str
    topic_keys: list[str] = Field(min_length=2, max_length=2)
    label: str | None = Field(default=None, max_length=120)
    requested_by: str = Field(min_length=1, max_length=120)
    request_key: str | None = Field(default=None, max_length=200)

    @field_validator("topic_keys", mode="after")
    @classmethod
    def _validate_topic_keys(cls, v: list[str]) -> list[str]:
        if len(set(v)) != 2:
            raise ValueError("topic_keys must contain exactly two distinct keys")
        if any(not k.strip() for k in v):
            raise ValueError("topic_keys cannot contain empty or whitespace-only keys")
        return v

    @field_validator("label", mode="after")
    @classmethod
    def _validate_label(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("label cannot be empty or whitespace")
        return v


class MergeQueueResponse(BaseModel):
    """合併排程回應 (202)。"""

    run_id: int
    workspace_id: int
    operation: str
    status: str


class MergeHistoryItem(BaseModel):
    """合併歷史項目。"""

    merge_run_id: int
    source_topics: list[str]
    result_topic: str
    can_unmerge: bool
    blocked_reason: str | None


class UnmergeRequest(BaseModel):
    """解除合併請求。"""

    source_field: str
    merge_run_id: int = Field(ge=1)
    requested_by: str = Field(min_length=1, max_length=120)
    request_key: str | None = Field(default=None, max_length=200)


class UnmergeQueueResponse(BaseModel):
    """解除合併排程回應 (202)。"""

    run_id: int
    workspace_id: int
    operation: str
    status: str


class RenameRequest(BaseModel):
    """重命名請求。"""

    label: str = Field(min_length=1, max_length=120)
    renamed_by: str = Field(min_length=1, max_length=120)

    @field_validator("label", "renamed_by", mode="after")
    @classmethod
    def _strip_and_validate(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("field cannot be empty or whitespace")
        return stripped


class RenameResponse(BaseModel):
    """重命名回應。"""

    topic_key: str
    label: str
    label_source: str


# ── Exception Mapping ──────────────────────────────────────────────


def _map_repo_error(exc: Exception) -> HTTPException:
    """將 Repository 例外對應至 HTTP 狀態碼。

    已知 domain exceptions 保持原訊息；未預期例外回傳通用 500，不洩漏內部細節。
    """
    if isinstance(exc, WorkspaceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, TopicNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, InvalidTopicOperationError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, TopicRepositoryUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    # 未預期錯誤：統一 500，不回傳原始例外訊息
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
    )


def _validate_source_field(source_field: str) -> None:
    """驗證 source_field 是否為白名單通道。"""
    try:
        get_source_spec(source_field)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


# ── Endpoints ──────────────────────────────────────────────────────


@router.get(
    "/workspaces/{workspace_id}/topics",
    response_model=TopicsListResponse,
    summary="列出指定 workspace 通道的主題",
)
def list_topics(
    workspace_id: Annotated[int, Path(ge=1)],
    source_field: str,
    repo: TopicRepository = Depends(get_topic_repository),
) -> TopicsListResponse:
    """取得主題列表。"""
    _validate_source_field(source_field)
    try:
        result: ListTopicsResult = repo.list_topics(workspace_id, source_field)
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return TopicsListResponse(
        workspace_id=workspace_id,
        source_field=source_field,
        run_id=result["run_id"],
        topics=[
            TopicResponse(
                topic_key=t["topic_key"],
                label=t["label"],
                summary=t["summary"],
                doc_count=t["doc_count"],
                keywords=t["keywords"],
                label_source=t["label_source"],
                display_order=t["display_order"],
                status=t["status"],
                merged_into_topic_key=t["merged_into_topic_key"],
            )
            for t in result["topics"]
        ],
    )


@router.get(
    "/workspaces/{workspace_id}/topics/merge-suggestions",
    response_model=MergeSuggestionsResponse,
    summary="取得相近主題合併建議",
)
def list_merge_suggestions(
    workspace_id: Annotated[int, Path(ge=1)],
    source_field: str,
    repo: TopicRepository = Depends(get_topic_repository),
) -> MergeSuggestionsResponse:
    """取得合併建議。"""
    _validate_source_field(source_field)
    try:
        result: ListMergeSuggestionsResult = repo.list_merge_suggestions(
            workspace_id, source_field
        )
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return MergeSuggestionsResponse(
        workspace_id=result["workspace_id"],
        source_field=result["source_field"],
        suggestions=[
            MergeSuggestionItem(
                topic_keys=s["topic_keys"],
                labels=s["labels"],
                distance=s["distance"],
            )
            for s in result["suggestions"]
        ],
    )


@router.post(
    "/workspaces/{workspace_id}/topics/merge",
    response_model=MergeQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="排程合併兩個主題",
)
def queue_merge(
    workspace_id: Annotated[int, Path(ge=1)],
    request: MergeRequest,
    repo: TopicRepository = Depends(get_topic_repository),
) -> MergeQueueResponse:
    """將合併操作排入背景工作佇列，回傳 202。

    規則：
    - topic_keys 必須恰好兩個不同 key
    - 兩個 topic 必須皆為 active
    """
    _validate_source_field(request.source_field)
    # Pydantic 已驗證長度為 2，這裡額外檢查不重複
    if len(set(request.topic_keys)) != 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="topic_keys must contain exactly two distinct keys",
        )
    try:
        result: MergeQueueResult = repo.queue_merge(
            workspace_id=workspace_id,
            source_field=request.source_field,
            topic_keys=request.topic_keys,
            label=request.label,
            requested_by=request.requested_by,
            request_key=request.request_key,
        )
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return MergeQueueResponse(
        run_id=result["run_id"],
        workspace_id=result["workspace_id"],
        operation=result["operation"],
        status=result["status"],
    )


@router.get(
    "/workspaces/{workspace_id}/topics/merge-history",
    response_model=list[MergeHistoryItem],
    summary="取得合併歷史紀錄",
)
def list_merge_history(
    workspace_id: Annotated[int, Path(ge=1)],
    source_field: str,
    repo: TopicRepository = Depends(get_topic_repository),
) -> list[MergeHistoryItem]:
    """取得合併歷史。"""
    _validate_source_field(source_field)
    try:
        result = repo.list_merge_history(workspace_id, source_field)
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return [
        MergeHistoryItem(
            merge_run_id=item["merge_run_id"],
            source_topics=item["source_topics"],
            result_topic=item["result_topic"],
            can_unmerge=item["can_unmerge"],
            blocked_reason=item["blocked_reason"],
        )
        for item in result
    ]


@router.post(
    "/workspaces/{workspace_id}/topics/unmerge",
    response_model=UnmergeQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="排程解除合併",
)
def queue_unmerge(
    workspace_id: Annotated[int, Path(ge=1)],
    request: UnmergeRequest,
    repo: TopicRepository = Depends(get_topic_repository),
) -> UnmergeQueueResponse:
    """將解除合併操作排入背景工作佇列，回傳 202。"""
    _validate_source_field(request.source_field)
    try:
        result: UnmergeQueueResult = repo.queue_unmerge(
            workspace_id=workspace_id,
            source_field=request.source_field,
            merge_run_id=request.merge_run_id,
            requested_by=request.requested_by,
            request_key=request.request_key,
        )
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return UnmergeQueueResponse(
        run_id=result["run_id"],
        workspace_id=result["workspace_id"],
        operation=result["operation"],
        status=result["status"],
    )


@router.patch(
    "/workspaces/{workspace_id}/topics/{topic_key}",
    response_model=RenameResponse,
    summary="人工重命名主題",
)
def rename_topic(
    workspace_id: Annotated[int, Path(ge=1)],
    topic_key: Annotated[str, Path(min_length=1, pattern=r"\S+")],
    request: RenameRequest,
    repo: TopicRepository = Depends(get_topic_repository),
) -> RenameResponse:
    """人工修改主題名稱，label_source 強制為 manual。

    topic_key 為穩定識別碼，source_field 由 repository 內部反查。
    """
    try:
        result: RenameResult = repo.rename_topic(
            workspace_id=workspace_id,
            topic_key=topic_key,
            label=request.label.strip(),
            renamed_by=request.renamed_by,
        )
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return RenameResponse(
        topic_key=result["topic_key"],
        label=result["label"],
        label_source=result["label_source"],
    )