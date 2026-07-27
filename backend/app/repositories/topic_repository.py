"""Topic Repository Protocol：定義正式 API 與實作之間的邊界。

- 不依賴 FastAPI、psycopg、DB schema
- 使用 TypedDict / dataclass 定義明確契約
- 實作端（尚未建立）需符合此 Protocol
- Fake 實作放在 tests/，不進 production
"""

from __future__ import annotations

from typing import Protocol, TypedDict


# ============================================================
# Domain Exceptions（對應 HTTP status code）
# ============================================================


class TopicNotFoundError(LookupError):
    """主題不存在 -> 404"""

    pass


class WorkspaceNotFoundError(LookupError):
    """Workspace 不存在 -> 404"""

    pass


class InvalidTopicOperationError(ValueError):
    """業務規則衝突（如合併非 active topic、重複合併等）-> 409"""

    pass


class TopicRepositoryUnavailableError(RuntimeError):
    """Repository 未配置或不可用 -> 503"""

    pass


# ============================================================
# Input / Output Contracts（TypedDict 保持序列化友善）
# ============================================================


class TopicDict(TypedDict):
    """單一主題回傳結構。"""

    topic_key: str
    label: str
    summary: str
    doc_count: int
    keywords: list[str]
    label_source: str  # "model" | "ai" | "manual"
    display_order: int
    status: str  # "active" | "merged"
    merged_into_topic_key: str | None


class ListTopicsResult(TypedDict):
    """GET /topics 回傳結構。"""

    workspace_id: int
    source_field: str
    run_id: int
    topics: list[TopicDict]


class MergeSuggestionItem(TypedDict):
    """合併建議項目。"""

    topic_keys: list[str]
    labels: list[str]
    distance: float


class ListMergeSuggestionsResult(TypedDict):
    """GET /topics/merge-suggestions 回傳結構。"""

    workspace_id: int
    source_field: str
    suggestions: list[MergeSuggestionItem]


class MergeQueueResult(TypedDict):
    """合併排程結果。"""

    run_id: int
    workspace_id: int
    operation: str  # "topic_merge"
    status: str  # "queued"


class MergeHistoryItem(TypedDict):
    """合併歷史項目。"""

    merge_run_id: int
    source_topics: list[str]
    result_topic: str
    # job 真實狀態（queued／running／succeeded／failed）：前端據此顯示「處理中／失敗」，
    # 不把未完成的合併當成已完成（2026-07-27 實機踩到 job 97 永遠 queued 卻顯示可解除）。
    status: str
    can_unmerge: bool
    blocked_reason: str | None


class UnmergeQueueResult(TypedDict):
    """解除合併排程結果。"""

    run_id: int
    workspace_id: int
    operation: str  # "topic_unmerge"
    status: str  # "queued"


class RenameResult(TypedDict):
    """重命名結果。"""

    topic_key: str
    label: str
    label_source: str  # 必為 "manual"


# ============================================================
# Protocol
# ============================================================


class TopicRepository(Protocol):
    """Topic Repository 介面。

    所有方法若發生預期內業務錯誤，拋出對應 Domain Exception；
    未預期錯誤（連線失敗等）拋出 TopicRepositoryUnavailableError。
    """

    def list_topics(
        self,
        workspace_id: int,
        source_field: str,
    ) -> ListTopicsResult:
        """列出指定 workspace、source_field 的主題清單。

        Raises:
            WorkspaceNotFoundError: workspace 不存在
            TopicRepositoryUnavailableError: 儲存層不可用
        """
        ...

    def list_merge_suggestions(
        self,
        workspace_id: int,
        source_field: str,
    ) -> ListMergeSuggestionsResult:
        """取得相近主題合併建議。

        Raises:
            WorkspaceNotFoundError: workspace 不存在
            TopicRepositoryUnavailableError: 儲存層不可用
        """
        ...

    def queue_merge(
        self,
        workspace_id: int,
        source_field: str,
        topic_keys: list[str],
        label: str | None,
        requested_by: str,
        request_key: str | None,
    ) -> MergeQueueResult:
        """排程合併兩個主題。

        Args:
            topic_keys: 必須恰好兩個不同 key
        Raises:
            InvalidTopicOperationError: topic 不存在、非 active、已合併、重複 key 等
            WorkspaceNotFoundError: workspace 不存在
            TopicRepositoryUnavailableError: 儲存層不可用
        """
        ...

    def list_merge_history(
        self,
        workspace_id: int,
        source_field: str,
    ) -> list[MergeHistoryItem]:
        """取得合併歷史紀錄。

        Raises:
            WorkspaceNotFoundError: workspace 不存在
            TopicRepositoryUnavailableError: 儲存層不可用
        """
        ...

    def queue_unmerge(
        self,
        workspace_id: int,
        source_field: str,
        merge_run_id: int,
        requested_by: str,
        request_key: str | None,
    ) -> UnmergeQueueResult:
        """排程解除合併。

        Raises:
            InvalidTopicOperationError: merge_run 不存在、不可 unmerge、已 unmerge 等
            WorkspaceNotFoundError: workspace 不存在
            TopicRepositoryUnavailableError: 儲存層不可用
        """
        ...

    def rename_topic(
        self,
        workspace_id: int,
        topic_key: str,
        label: str,
        renamed_by: str,
    ) -> RenameResult:
        """人工重命名主題，label_source 強制為 manual。

        Raises:
            TopicNotFoundError: topic 不存在或非 active
            InvalidTopicOperationError: label 為空等
            TopicRepositoryUnavailableError: 儲存層不可用
        """
        ...


# ============================================================
# Dependency Injection Helper
# ============================================================


async def get_topic_repository() -> TopicRepository:
    """FastAPI Dependency：取得 TopicRepository 實例。

    正式環境預設注入 PostgresTopicRepository（對齊 0021 schema）。若儲存層真的不可用，
    Postgres 實作在各方法內把 psycopg.Error 轉為 TopicRepositoryUnavailableError，
    由 exception handler 轉為 503——保留「repository 不可用時回 503」的防呆語意。

    測試時以 `app.dependency_overrides[get_topic_repository] = lambda: FakeTopicRepository()`
    注入替身。實作在此函式內延遲 import，避免 topic_repository 模組反向依賴 psycopg。
    """
    from backend.app.repositories.postgres_topic_repository import PostgresTopicRepository

    return PostgresTopicRepository()