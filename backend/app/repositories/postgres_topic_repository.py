"""PostgresTopicRepository：TopicRepository Protocol 對 0021 3+3 schema 的真實實作。

邊界原則：
- 所有 SQL 留在本 repository，不進 API 層（API 只透過 Protocol 操作）。
- 讀取面（list_topics/list_merge_suggestions）委派唯讀基底 PostgresTopicStateRepository，
  只回合併／改名後的 active 主題＋未分類，候選不外洩。
- 寫入面（queue_merge/queue_unmerge）只 enqueue 一筆 queued app_layer.workflow_runs，
  不執行實際合併；request_key 冪等（重複回既有 run，不重排）。
- rename_topic 直接更新最新 topic_run 的 topic_state_json 內該主題 label，強制 label_source='manual'。
- workspace 不存在 → WorkspaceNotFoundError；連線／未預期 DB 錯誤 → TopicRepositoryUnavailableError。

資料落點對照 0021 schema：
- app_layer.workflow_runs：run_type='topic_merge'/'topic_unmerge' 為 queued 佇列；request_json 帶參數，
  request_key UNIQUE 提供冪等。
- derived_layer.topic_runs.topic_state_json：主題狀態唯一來源（rename 就地更新）。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from backend.app.repositories.topic_repository import (
    InvalidTopicOperationError,
    ListMergeSuggestionsResult,
    ListTopicsResult,
    MergeHistoryItem,
    MergeQueueResult,
    RenameResult,
    TopicDict,
    TopicNotFoundError,
    TopicRepositoryUnavailableError,
    UnmergeQueueResult,
    WorkspaceNotFoundError,
)
from backend.app.repositories.topic_state_repository import (
    PostgresTopicStateRepository,
    TopicStateNotFoundError,
)


class PostgresTopicRepository:
    """以 psycopg 直操 0021 schema 的 TopicRepository 實作。"""

    def __init__(self, connect_kwargs: dict[str, Any] | None = None):
        # 未指定時沿用專案統一連線設定（env PG* / DATABASE_URL）；與唯讀基底共用同一組設定
        self._connect_kwargs = connect_kwargs
        self._state_repo = PostgresTopicStateRepository(connect_kwargs)

    def _connect(self):
        from backend.app.db.connection import get_connection_kwargs

        return psycopg.connect(**(self._connect_kwargs or get_connection_kwargs()))

    @staticmethod
    def _assert_workspace_exists(conn, workspace_id: int) -> None:
        """workspace 不存在即拋 WorkspaceNotFoundError（404）。"""
        row = conn.execute(
            "SELECT 1 FROM app_layer.workspaces WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} not found")

    def _fetch_raw_topics(self, run_id: int) -> dict[str, dict[str, Any]]:
        """讀指定 topic_run 的原始 topic 條目（供 list_topics 補齊顯示欄位），以 topic_code 索引。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT topic_state_json FROM derived_layer.topic_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        state = (row[0] if row else {}) or {}
        return {t["topic_code"]: t for t in state.get("topics", []) if t.get("topic_code")}

    # ── 讀取面 ────────────────────────────────────────────────────

    def list_topics(self, workspace_id: int, source_field: str) -> ListTopicsResult:
        """列出最新正式 run 的 active 主題（含未分類）；ws 無 run 時回空清單、run_id=0。"""
        try:
            with self._connect() as conn:
                self._assert_workspace_exists(conn, workspace_id)
            try:
                state = self._state_repo.get_latest_topic_state(workspace_id, source_field)
            except TopicStateNotFoundError:
                # ws 存在但該通道尚無 topic run：回空主題，run_id=0 表示尚無 run
                return ListTopicsResult(
                    workspace_id=workspace_id, source_field=source_field, run_id=0, topics=[])
            # 顯示欄位（summary/keywords/label_source…）取自帶 topics 的 run，
            # 非 assignments 基準 run（incremental run 不帶 topics）
            raw = self._fetch_raw_topics(state["state_run_id"])
            topics: list[TopicDict] = []
            for order, t in enumerate(state["topics"]):
                extra = raw.get(t["topic_code"], {})
                topics.append(TopicDict(
                    topic_key=t["topic_code"],
                    label=t.get("label") or "",
                    summary=extra.get("summary") or "",
                    doc_count=len(t["patent_ids"]),  # 併回後實際 assignment 數，較 state doc_count 精準
                    keywords=list(extra.get("keywords") or []),
                    label_source=extra.get("label_source") or "model",
                    display_order=extra.get("display_order", order),
                    status="active",
                    merged_into_topic_key=None,
                ))
            return ListTopicsResult(
                workspace_id=workspace_id, source_field=source_field,
                run_id=state["run_id"], topics=topics)
        except psycopg.Error as exc:
            raise TopicRepositoryUnavailableError("topic repository backend error") from exc

    def list_merge_suggestions(
        self, workspace_id: int, source_field: str
    ) -> ListMergeSuggestionsResult:
        """合併建議：v1 尚無主題相似度來源，驗 workspace 後回空清單（不捏造建議）。"""
        try:
            with self._connect() as conn:
                self._assert_workspace_exists(conn, workspace_id)
            return ListMergeSuggestionsResult(
                workspace_id=workspace_id, source_field=source_field, suggestions=[])
        except psycopg.Error as exc:
            raise TopicRepositoryUnavailableError("topic repository backend error") from exc

    # ── 寫入面（enqueue queued run）────────────────────────────────

    def queue_merge(
        self,
        workspace_id: int,
        source_field: str,
        topic_keys: list[str],
        label: str | None,
        requested_by: str,
        request_key: str | None,
    ) -> MergeQueueResult:
        """排程合併：驗兩不同非空 active key、目標主題存在且 active，enqueue queued run（request_key 冪等）。"""
        if len(topic_keys) != 2 or any(not k or not k.strip() for k in topic_keys) \
                or len(set(topic_keys)) != 2:
            raise InvalidTopicOperationError(
                "merge requires exactly two distinct non-empty topic_keys")
        try:
            with self._connect() as conn:
                self._assert_workspace_exists(conn, workspace_id)
            # 目標主題必須存在且 active（取最新正式 run 的 active 主題集合）
            try:
                state = self._state_repo.get_latest_topic_state(workspace_id, source_field)
            except TopicStateNotFoundError as exc:
                raise InvalidTopicOperationError("no active topic run to merge") from exc
            active = {t["topic_code"] for t in state["topics"]}
            missing = [k for k in topic_keys if k not in active]
            if missing:
                raise InvalidTopicOperationError(f"topics not active or not found: {missing}")

            request_json = {"source_field": source_field, "topic_keys": list(topic_keys),
                            "label": label, "requested_by": requested_by}
            run_id, status = self._enqueue_run(
                "topic_merge", workspace_id, request_key, request_json)
            return MergeQueueResult(
                run_id=run_id, workspace_id=workspace_id, operation="topic_merge", status=status)
        except psycopg.Error as exc:
            raise TopicRepositoryUnavailableError("topic repository backend error") from exc

    def queue_unmerge(
        self,
        workspace_id: int,
        source_field: str,
        merge_run_id: int,
        requested_by: str,
        request_key: str | None,
    ) -> UnmergeQueueResult:
        """排程解除合併：merge_run 必須屬本 workspace 且尚未 unmerge；request_key 冪等。"""
        try:
            with self._connect() as conn:
                self._assert_workspace_exists(conn, workspace_id)
                merge_run = conn.execute(
                    "SELECT workspace_id FROM app_layer.workflow_runs "
                    "WHERE run_id = %s AND run_type = 'topic_merge'",
                    (merge_run_id,),
                ).fetchone()
                if merge_run is None or merge_run[0] != workspace_id:
                    raise InvalidTopicOperationError(
                        f"merge_run {merge_run_id} not found for workspace {workspace_id}")
                # request_key 冪等優先於「已 unmerge」檢查，避免重送同一請求誤判衝突
                if request_key:
                    existing = conn.execute(
                        "SELECT run_id, status FROM app_layer.workflow_runs WHERE request_key = %s",
                        (request_key,),
                    ).fetchone()
                    if existing is not None:
                        return UnmergeQueueResult(
                            run_id=existing[0], workspace_id=workspace_id,
                            operation="topic_unmerge", status=existing[1])
                already = conn.execute(
                    "SELECT run_id FROM app_layer.workflow_runs "
                    "WHERE run_type = 'topic_unmerge' "
                    "AND (request_json->>'merge_run_id')::bigint = %s",
                    (merge_run_id,),
                ).fetchone()
                if already is not None:
                    raise InvalidTopicOperationError(
                        f"merge_run {merge_run_id} already unmerged")

                request_json = {"source_field": source_field, "merge_run_id": merge_run_id,
                                "requested_by": requested_by}
                run_id = conn.execute(
                    "INSERT INTO app_layer.workflow_runs "
                    "(workspace_id, run_type, status, request_key, request_json) "
                    "VALUES (%s, 'topic_unmerge', 'queued', %s, %s) RETURNING run_id",
                    (workspace_id, request_key, Jsonb(request_json)),
                ).fetchone()[0]
                conn.commit()
            return UnmergeQueueResult(
                run_id=run_id, workspace_id=workspace_id,
                operation="topic_unmerge", status="queued")
        except psycopg.Error as exc:
            raise TopicRepositoryUnavailableError("topic repository backend error") from exc

    def list_merge_history(
        self, workspace_id: int, source_field: str
    ) -> list[MergeHistoryItem]:
        """由 topic_merge/unmerge run 組裝合併歷史，並反映 job 真實狀態。

        can_unmerge=False 的兩種情形：
        1. 已被對應的 topic_unmerge 引用（already_unmerged）。
        2. **該 merge job 尚未成功**（2026-07-27 修）——queued／running 表示還沒合併、
           failed 表示合併沒發生，都不該提供「解除合併」。

        ⚠ 原實作撈所有 topic_merge run、**完全不看 status**，實機踩到：job 97 永遠
        queued（當時沒 worker 領），兩個主題原封不動，畫面卻顯示合併完成＋可解除。
        「歷史」的語意是已經發生的事；未完成者仍列出（使用者按了要有回饋），
        但明確標示狀態、不給解除鈕。
        """
        try:
            with self._connect() as conn:
                self._assert_workspace_exists(conn, workspace_id)
                merges = conn.execute(
                    "SELECT run_id, request_json, status FROM app_layer.workflow_runs "
                    "WHERE workspace_id = %s AND run_type = 'topic_merge' "
                    "AND request_json->>'source_field' = %s ORDER BY run_id",
                    (workspace_id, source_field),
                ).fetchall()
                unmerged = {
                    r[0] for r in conn.execute(
                        "SELECT (request_json->>'merge_run_id')::bigint "
                        "FROM app_layer.workflow_runs "
                        "WHERE workspace_id = %s AND run_type = 'topic_unmerge' "
                        "AND request_json->>'merge_run_id' IS NOT NULL",
                        (workspace_id,),
                    ).fetchall()
                }
            items: list[MergeHistoryItem] = []
            for run_id, req, status in merges:
                req = req or {}
                source_topics = list(req.get("topic_keys") or [])
                # 合併採「併入第一個 key」慣例，結果主題即來源首鍵。
                result_topic = source_topics[0] if source_topics else ""
                # 只有真正跑完的合併才可解除；未完成者列出但不給解除鈕（見 docstring）。
                if status != "succeeded":
                    can_unmerge = False
                    blocked_reason = (
                        "merge_failed" if status == "failed" else "merge_in_progress")
                elif run_id in unmerged:
                    can_unmerge = False
                    blocked_reason = "already_unmerged"
                else:
                    can_unmerge = True
                    blocked_reason = None
                items.append(MergeHistoryItem(
                    merge_run_id=run_id,
                    source_topics=source_topics,
                    result_topic=result_topic,
                    status=status,
                    can_unmerge=can_unmerge,
                    blocked_reason=blocked_reason,
                ))
            return items
        except psycopg.Error as exc:
            raise TopicRepositoryUnavailableError("topic repository backend error") from exc

    def rename_topic(
        self, workspace_id: int, topic_key: str, label: str, renamed_by: str
    ) -> RenameResult:
        """人工改名：就地更新最新 run（每 source_field 各取最新）內該 active 主題 label，強制 manual。"""
        if not label or not label.strip():
            raise InvalidTopicOperationError("label cannot be empty")
        new_label = label.strip()
        try:
            with self._connect() as conn:
                self._assert_workspace_exists(conn, workspace_id)
                rows = conn.execute(
                    "SELECT tr.run_id, tr.source_field, tr.topic_state_json "
                    "FROM derived_layer.topic_runs tr "
                    "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
                    "WHERE wr.workspace_id = %s ORDER BY tr.run_id DESC",
                    (workspace_id,),
                ).fetchall()
                # 只認每個 source_field 的最新 run（首次出現者），在其 active 主題中找 topic_key
                seen: set[str] = set()
                target = None
                for run_id, sf, state in rows:
                    if sf in seen:
                        continue
                    seen.add(sf)
                    topics = list((state or {}).get("topics") or [])
                    for t in topics:
                        if t.get("topic_code") == topic_key and t.get("status") == "active":
                            target = (run_id, state, topics)
                            break
                    if target is not None:
                        break
                if target is None:
                    raise TopicNotFoundError(
                        f"active topic {topic_key} not found in workspace {workspace_id}")

                run_id, state, topics = target
                new_topics = []
                for t in topics:
                    if t.get("topic_code") == topic_key:
                        t = {**t, "label": new_label, "label_source": "manual"}
                    new_topics.append(t)
                new_state = {**(state or {}), "topics": new_topics}
                conn.execute(
                    "UPDATE derived_layer.topic_runs SET topic_state_json = %s WHERE run_id = %s",
                    (Jsonb(new_state), run_id),
                )
                conn.commit()
            return RenameResult(topic_key=topic_key, label=new_label, label_source="manual")
        except psycopg.Error as exc:
            raise TopicRepositoryUnavailableError("topic repository backend error") from exc

    # ── 內部 helper ──────────────────────────────────────────────

    def _enqueue_run(
        self, run_type: str, workspace_id: int, request_key: str | None,
        request_json: dict[str, Any],
    ) -> tuple[int, str]:
        """寫入一筆 queued workflow_run；request_key 已存在則回既有 run（冪等，不重排）。

        回傳 (run_id, status)：新排程為 (run_id, 'queued')，冪等命中為既有 run 的實際狀態。
        """
        with self._connect() as conn:
            if request_key:
                existing = conn.execute(
                    "SELECT run_id, status FROM app_layer.workflow_runs WHERE request_key = %s",
                    (request_key,),
                ).fetchone()
                if existing is not None:
                    return existing[0], existing[1]
            try:
                run_id = conn.execute(
                    "INSERT INTO app_layer.workflow_runs "
                    "(workspace_id, run_type, status, request_key, request_json) "
                    "VALUES (%s, %s, 'queued', %s, %s) RETURNING run_id",
                    (workspace_id, run_type, request_key, Jsonb(request_json)),
                ).fetchone()[0]
                conn.commit()
                return run_id, "queued"
            except psycopg.errors.UniqueViolation:
                # 併發下同一 request_key 已被搶先寫入：回滾後回既有 run，維持冪等
                conn.rollback()
                existing = conn.execute(
                    "SELECT run_id, status FROM app_layer.workflow_runs WHERE request_key = %s",
                    (request_key,),
                ).fetchone()
                if existing is not None:
                    return existing[0], existing[1]
                raise
