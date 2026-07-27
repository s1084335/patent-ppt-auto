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

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator

from backend.app.app_layer import workspace_queries
from backend.app.clustering.sources import get_source_spec
from backend.app.db import job_repository
from backend.app.repositories.topic_state_repository import (
    PostgresTopicStateRepository,
    TopicStateNotFoundError,
)
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


class TopicKeywordResponse(BaseModel):
    """單一 c-TF-IDF 關鍵詞（詞＋權重）。

    落點對齊寫入端 clustering/runner.py `_persist_final_topics`：
    keywords 存成 [{"term": str, "weight": float}, ...]。
    weight 供排序／顯示用，不可為了型別簡化而丟棄。
    """

    term: str
    weight: float | None = None


class TopicResponse(BaseModel):
    """單一主題回應。"""

    topic_key: str
    label: str
    summary: str
    doc_count: int
    # ⚠ 必須是物件而非 list[str]：寫入端存 {term, weight}，
    # 早期宣告成 list[str] 時，只要 run 有正式 topics，list_topics 就 500
    # （2026-07-27 實機故障：ValidationError 10 errors for TopicResponse）。
    keywords: list[TopicKeywordResponse]
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


class TopicPatentItem(BaseModel):
    """topic 專利明細單筆（分類區點主題後列出）。"""

    patent_id: int
    patent_number: str | None
    title: str | None
    country_code: str | None
    applicant_display_name: str | None


class TopicPatentsResponse(BaseModel):
    """某 topic 指派專利分頁回應。"""

    workspace_id: int
    source_field: str
    topic_key: str
    total: int
    limit: int
    offset: int
    items: list[TopicPatentItem]


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


class AiLabelRequest(BaseModel):
    """AI 主題標籤／摘要任務請求。

    只帶識別資訊與 CLI 選項；代表性專利文檔在 AI bridge 端才批次取（見
    worker/ai_topic_label_runner.py），不在 HTTP 請求執行緒內組大 payload。
    """

    source_field: str
    # 不給＝該通道全部 active 主題；給了只重跑指定主題（例如人工覺得某幾個名字不好）。
    topic_keys: list[str] | None = None
    cli_kind: str = Field(default="claude", max_length=32)
    model: str | None = Field(default=None, max_length=120)
    requested_by: str = Field(default="web-user", min_length=1, max_length=120)
    request_key: str | None = Field(default=None, max_length=200)


class AiLabelQueueResponse(BaseModel):
    """AI 標籤任務排程回應 (202)。"""

    run_id: int
    workspace_id: int
    job_type: str
    status: str
    poll_url: str


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


def get_topic_state_repository() -> PostgresTopicStateRepository:
    """FastAPI Dependency：唯讀 topic 狀態 repository（topic→patents 指派唯一來源）。

    測試可以 app.dependency_overrides 覆寫；正式走預設連線設定。
    """
    return PostgresTopicStateRepository()


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
    "/workspaces/{workspace_id}/topics/{topic_key}/patents",
    response_model=TopicPatentsResponse,
    summary="列出指派到某主題的專利（分類區點主題後列出）",
)
def list_topic_patents(
    workspace_id: Annotated[int, Path(ge=1)],
    topic_key: Annotated[str, Path(min_length=1, pattern=r"\S+")],
    source_field: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    state_repo: PostgresTopicStateRepository = Depends(get_topic_state_repository),
) -> TopicPatentsResponse:
    """回傳該 topic 指派到的專利明細（分頁）。

    topic→patents 走 topic_state_repository 的指派關係（非 label 文字比對）。workspace 不存在
    或該通道尚無分群、或 topic_key 非 active 主題 → 404；找到則以 patent_ids 交集 workspace
    成員取明細。source_field 需為白名單通道。
    """
    _validate_source_field(source_field)
    # workspace 存在性：不存在直接 404（區分「無此 ws」與「有 ws 但無此 topic」）。
    if workspace_queries.get_workspace_detail(workspace_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workspace not found: {workspace_id}",
        )
    try:
        state = state_repo.get_latest_topic_state(workspace_id, source_field)
    except TopicStateNotFoundError:
        # ws 存在但該通道尚無分群 run：等同 topic 不存在。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"topic not found: {topic_key}",
        )
    topic = next((t for t in state.get("topics", []) if t.get("topic_code") == topic_key), None)
    if topic is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"topic not found: {topic_key}",
        )
    result = workspace_queries.list_topic_patents(
        workspace_id=workspace_id,
        patent_ids=[int(pid) for pid in topic.get("patent_ids", [])],
        limit=limit,
        offset=offset,
    )
    return TopicPatentsResponse(
        workspace_id=workspace_id,
        source_field=source_field,
        topic_key=topic_key,
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        items=[TopicPatentItem(**it) for it in result["items"]],
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


@router.post(
    "/workspaces/{workspace_id}/topics/ai-label",
    response_model=AiLabelQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="為正式 topic version 建立 AI 標籤／摘要任務",
)
def queue_ai_label(
    workspace_id: Annotated[int, Path(ge=1)],
    request: AiLabelRequest,
) -> AiLabelQueueResponse:
    """把主題標籤／摘要排入 AI 佇列，回 202；由 host-side ai_bridge 領取執行。

    用途：正式 topic version 的主題名現況是 c-TF-IDF 關鍵詞拼接（如 "unit / said / second"），
    人看不懂。本任務讓 CLI 讀每個主題「c-TF-IDF 衡量出的前 5 筆代表性專利」的文檔內容後，
    產出中文標籤與摘要草稿。

    🔴 payload 只帶識別資訊（workspace_id／source_field／topic_keys）與 CLI 選項，
    **不含 keywords**——關鍵詞內容一律不得傳給 CLI（使用者定案）；代表性專利文檔由
    ai_topic_label_runner 在 bridge 端批次取出，也同樣不帶 keywords。

    產出是**草稿**：回填時一律 label_source='llm'，人工命名（manual）不會被覆蓋。
    """
    _validate_source_field(request.source_field)
    if workspace_queries.get_workspace_detail(workspace_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workspace not found: {workspace_id}",
        )
    payload = {
        "workspace_id": workspace_id,
        "source_field": request.source_field,
        "cli_kind": request.cli_kind,
        "requested_by": request.requested_by,
    }
    if request.topic_keys:
        payload["topic_keys"] = list(request.topic_keys)
    if request.model:
        payload["model"] = request.model
    try:
        job = job_repository.create_job(
            "ai:topic_label",
            payload=payload,
            workspace_id=workspace_id,
            idempotency_key=request.request_key,
            # AI CLI 任務不自動重試：重跑要花 LLM 額度，失敗由使用者決定是否再送。
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise _map_repo_error(exc) from exc
    return AiLabelQueueResponse(
        run_id=job.job_id,
        workspace_id=workspace_id,
        job_type=job.job_type,
        status=job.status,
        poll_url=f"/api/v1/jobs/{job.job_id}",
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