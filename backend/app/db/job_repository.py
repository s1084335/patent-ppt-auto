"""app_layer.workflow_runs 的單一佇列存取層（backend＋worker 共用，0021 遷移版）。

分工定案（2026-07-17 方案 A）：佇列所有 DB 存取集中本檔，backend 與 worker 都 import。
0021 遷移（對映表見 work-log「Worker 遷移輪」）：
- 佇列表 processing_jobs → app_layer.workflow_runs（欄位精簡至 7 欄）。
- job_id→run_id、job_type→run_type、payload_json→request_json、idempotency_key→request_key
  （UNIQUE，ON CONFLICT (request_key)）。
- worker 簿記（progress/current_stage/attempt_count/max_attempts/locked_by/locked_at/
  heartbeat_at/started_at/finished_at/error_message）全收 worker_state_json（JSONB）。
- 結果落 app_layer.workflow_outputs（版本化，用 PostgresWorkflowOutputsRepository，不自寫 SQL）；
  output_type='job_result:'||run_type。ProcessingJob.result_json 薄轉接由 outputs 讀回。
- claim ORDER BY run_id 走既有 idx_workflow_runs_claim (status, run_id)。

correctness-critical 的原子領取（FOR UPDATE SKIP LOCKED）與逾時回收由 schema 擁有者維護。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs, get_pool
from backend.app.repositories.workflow_outputs_repository import (
    PostgresWorkflowOutputsRepository,
)

# 需要外部 AI CLI 的工作類型（唯一事實來源）。
# 這些任務只由 host-side ai_bridge 領取，一般 worker 不領——一般 worker 容器沒有
# Claude／OpenCode CLI，領到也跑不動。分工實作：
#   worker/runner.py  DEFAULT_WORKER_JOB_TYPES = JOB_TYPES - AI_JOB_TYPES
#   worker/ai_bridge.py AI_JOB_TYPES 直接沿用本常數
# 新增 AI 任務類型只改這裡一處，兩端自動同步，不再各自維護字面值而漂移。
AI_JOB_TYPES: frozenset[str] = frozenset(
    {
        # 報表解讀敘述（既有）。
        "ai:narrative",
        # 主題標籤／摘要：把 c-TF-IDF 關鍵詞拼接的主題名換成人看得懂的中文名。
        # CLI 只讀每主題前 5 筆代表性專利的文檔內容，不給 keywords（使用者定案）。
        "ai:topic_label",
        # 文獻備註：AI 讀專利獨立項（patents."主權項"）摘要成備註，寫回
        # patent_attributes."文獻備註"。批次按字數切（獨立項最長逾萬字），不按件數。
        "ai:patent_note",
        # 候選方案 AI 輔助說明：calibrate 完成後 AI 讀三組候選的指標
        # （coherence／diversity／balance／score／k／document_count，不含專利內容/keywords/refs）
        # 產生取捨說明，寫回 topic_state_json->'candidates' 的 llm_explanation。
        "ai:candidate_explanation",
    }
)

# 合法工作類型（DB 不設 run_type check；backend 建立時由此白名單驗證）。
# 佇列亦承載 topic_merge/topic_unmerge（由 PostgresTopicRepository 直接寫入，不經 create_job）。
JOB_TYPES: frozenset[str] = frozenset(
    {
        "clustering_calibrate",
        "clustering_finalize",
        "clustering_incremental",
        "report_generate",
        "patent_import",
        "case_comparison",
        # 匯入後補算 embeddings（technical/effect）；複用既有 write_patent_embeddings，只算缺的。
        "embeddings",
    }
) | AI_JOB_TYPES

TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

# workflow_runs 選欄（統一給 ProcessingJob.from_row）。
_SELECT_COLUMNS = "run_id, run_type, status, workspace_id, request_json, worker_state_json"

# 結果 output_type 前綴（自取設計，2026-07-21 採用）。
_RESULT_OUTPUT_PREFIX = "job_result:"


def _result_output_type(run_type: str) -> str:
    return f"{_RESULT_OUTPUT_PREFIX}{run_type}"


def _request_fingerprint(*, job_type: str, payload: dict[str, Any],
                         workspace_id: int | None, max_attempts: int) -> str:
    """依請求內容產生穩定指紋，讓 idempotency key 可區分不同請求。"""
    canonical = {"job_type": job_type, "payload": payload,
                 "workspace_id": workspace_id, "max_attempts": max_attempts}
    data = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _effective_idempotency_key(*, idempotency_key: str | None, job_type: str,
                               payload: dict[str, Any], workspace_id: int | None,
                               max_attempts: int) -> str | None:
    """把使用者 key 與請求指紋合成實際入庫 request_key；未帶 key 時維持 NULL。"""
    if idempotency_key is None:
        return None
    fingerprint = _request_fingerprint(
        job_type=job_type, payload=payload, workspace_id=workspace_id, max_attempts=max_attempts)
    return f"{idempotency_key}:{fingerprint}"


@dataclass(frozen=True)
class ProcessingJob:
    """佇列單筆工作（backend 與 worker 共用；欄位相容 processing_jobs 舊介面）。"""

    job_id: int
    job_type: str
    status: str
    workspace_id: int | None
    payload_json: dict[str, Any]
    result_json: dict[str, Any] | None
    progress_percent: int
    current_stage: str
    attempt_count: int
    max_attempts: int
    # 失敗／逾時可讀原因（worker fail_job／requeue 寫入 worker_state_json.error_message）；
    # 前端失敗卡讀此欄顯示原因，未失敗時為 None。預設 None 以相容既有具名建構點。
    error_message: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ProcessingJob":
        """把 workflow_runs dict row 轉為內部資料物件；簿記欄由 worker_state_json 還原。"""
        state = dict(row["worker_state_json"] or {})
        error_message = state.get("error_message")
        return cls(
            job_id=int(row["run_id"]),
            job_type=str(row["run_type"]),
            status=str(row["status"]),
            workspace_id=int(row["workspace_id"]) if row["workspace_id"] is not None else None,
            payload_json=dict(row["request_json"] or {}),
            result_json=None,  # 結果落 workflow_outputs；需要時由 fetch_job_result 讀回
            progress_percent=int(state.get("progress_percent", 0)),
            current_stage=str(state.get("current_stage", "")),
            attempt_count=int(state.get("attempt_count", 0)),
            max_attempts=int(state.get("max_attempts", 3)),
            error_message=str(error_message) if error_message is not None else None,
        )


def fetch_job_result(run_id: int, run_type: str) -> dict[str, Any] | None:
    """讀回某 run 最新一版工作結果（自 workflow_outputs），無則 None。"""
    output = PostgresWorkflowOutputsRepository().get_output(run_id, _result_output_type(run_type))
    return output["data_json"] if output else None


# ── backend 端：建立、查詢、取消 ───────────────────────────────


def create_job(job_type: str, payload: dict[str, Any] | None = None, *,
               workspace_id: int | None = None, idempotency_key: str | None = None,
               max_attempts: int = 3) -> ProcessingJob:
    """建立一筆 queued 工作；帶 idempotency_key 且已存在時回既有工作，不重建。"""
    if job_type not in JOB_TYPES:
        raise ValueError(f"unsupported job_type: {job_type}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    payload_dict = payload or {}
    request_key = _effective_idempotency_key(
        idempotency_key=idempotency_key, job_type=job_type, payload=payload_dict,
        workspace_id=workspace_id, max_attempts=max_attempts)
    initial_state = {"attempt_count": 0, "max_attempts": max_attempts,
                     "progress_percent": 0, "current_stage": "queued"}
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO app_layer.workflow_runs
                    (run_type, request_json, workspace_id, request_key, worker_state_json)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (request_key) WHERE request_key IS NOT NULL DO NOTHING
                RETURNING {_SELECT_COLUMNS}
                """,
                (job_type, Jsonb(payload_dict), workspace_id, request_key, Jsonb(initial_state)),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM app_layer.workflow_runs WHERE request_key = %s",
                    (request_key,))
                row = cur.fetchone()
        conn.commit()
    return ProcessingJob.from_row(row)


def get_job(job_id: int) -> ProcessingJob | None:
    """依 run_id 取單筆工作；不存在回 None。"""
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM app_layer.workflow_runs WHERE run_id = %s",
                (job_id,))
            row = cur.fetchone()
    return ProcessingJob.from_row(row) if row is not None else None


def list_jobs(*, workspace_id: int | None = None, status: str | None = None,
              limit: int = 50) -> list[ProcessingJob]:
    """列出工作（新到舊＝run_id DESC），可依 workspace 或狀態過濾。"""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    conditions: list[str] = []
    params: list[Any] = []
    if workspace_id is not None:
        conditions.append("workspace_id = %s")
        params.append(workspace_id)
    if status is not None:
        conditions.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(int(limit))
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM app_layer.workflow_runs "
                f"{where} ORDER BY run_id DESC LIMIT %s", params)
            rows = cur.fetchall()
    return [ProcessingJob.from_row(row) for row in rows]


def cancel_job(job_id: int) -> ProcessingJob | None:
    """backend 端請求取消：queued/running 收斂為 cancelled；已終態則不動、回現況。"""
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE app_layer.workflow_runs
                SET status = 'cancelled',
                    worker_state_json = worker_state_json
                        || jsonb_build_object('current_stage', 'cancelled',
                                              'finished_at', to_jsonb(now()))
                WHERE run_id = %s AND status IN ('queued', 'running')
                RETURNING {_SELECT_COLUMNS}
                """,
                (job_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM app_layer.workflow_runs WHERE run_id = %s",
                    (job_id,))
                row = cur.fetchone()
        conn.commit()
    return ProcessingJob.from_row(row) if row is not None else None


# ── worker 端：領取、心跳、完成、失敗、取消收斂、回收 ───────────


class WorkerQueueClient:
    """worker 對 app_layer.workflow_runs 的所有寫入規則。"""

    def claim_next_job(
        self,
        *,
        worker_id: str,
        job_types: tuple[str, ...] | list[str] | set[str] | None = None,
    ) -> ProcessingJob | None:
        """FOR UPDATE SKIP LOCKED 原子領取下一筆 queued 工作（ORDER BY run_id 走 claim 索引）。"""
        # job_types=None 保留舊行為；有指定時只 claim 該類任務。
        job_type_filter = tuple(str(item) for item in (job_types or ()))
        unknown_types = sorted(set(job_type_filter) - JOB_TYPES)
        if unknown_types:
            raise ValueError(f"unsupported job_types: {', '.join(unknown_types)}")
        type_clause = "AND run_type = ANY(%s::text[])" if job_type_filter else ""
        params: list[Any] = []
        if job_type_filter:
            params.append(list(job_type_filter))
        params.append(worker_id)
        with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH next_job AS (
                        SELECT run_id FROM app_layer.workflow_runs
                        WHERE status = 'queued'
                          {type_clause}
                          AND COALESCE((worker_state_json->>'attempt_count')::int, 0)
                              < COALESCE((worker_state_json->>'max_attempts')::int, 3)
                        ORDER BY run_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE app_layer.workflow_runs AS r
                    SET status = 'running',
                        worker_state_json = r.worker_state_json || jsonb_build_object(
                            'locked_by', %s::text,
                            'locked_at', to_jsonb(now()),
                            'heartbeat_at', to_jsonb(now()),
                            'started_at', COALESCE(r.worker_state_json->'started_at', to_jsonb(now())),
                            'attempt_count', COALESCE((r.worker_state_json->>'attempt_count')::int, 0) + 1,
                            'current_stage', 'starting',
                            'error_message', NULL)
                    FROM next_job
                    WHERE r.run_id = next_job.run_id
                    RETURNING {', '.join('r.' + c for c in _SELECT_COLUMNS.split(', '))}
                    """,
                    params)
                row = cur.fetchone()
        return ProcessingJob.from_row(dict(row)) if row is not None else None

    def claim_job_by_id(
        self,
        *,
        job_id: int,
        worker_id: str,
        job_types: tuple[str, ...] | list[str] | set[str] | None = None,
    ) -> ProcessingJob | None:
        """Claim 指定 run_id 的 queued job；DB smoke 用它避免吃掉正式 AI 任務。"""
        job_type_filter = tuple(str(item) for item in (job_types or ()))
        unknown_types = sorted(set(job_type_filter) - JOB_TYPES)
        if unknown_types:
            raise ValueError(f"unsupported job_types: {', '.join(unknown_types)}")
        type_clause = "AND run_type = ANY(%s::text[])" if job_type_filter else ""
        params: list[Any] = [int(job_id)]
        if job_type_filter:
            params.append(list(job_type_filter))
        params.append(worker_id)
        with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH target_job AS (
                        SELECT run_id FROM app_layer.workflow_runs
                        WHERE run_id = %s
                          AND status = 'queued'
                          {type_clause}
                          AND COALESCE((worker_state_json->>'attempt_count')::int, 0)
                              < COALESCE((worker_state_json->>'max_attempts')::int, 3)
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE app_layer.workflow_runs AS r
                    SET status = 'running',
                        worker_state_json = r.worker_state_json || jsonb_build_object(
                            'locked_by', %s::text,
                            'locked_at', to_jsonb(now()),
                            'heartbeat_at', to_jsonb(now()),
                            'started_at', COALESCE(r.worker_state_json->'started_at', to_jsonb(now())),
                            'attempt_count', COALESCE((r.worker_state_json->>'attempt_count')::int, 0) + 1,
                            'current_stage', 'starting',
                            'error_message', NULL)
                    FROM target_job
                    WHERE r.run_id = target_job.run_id
                    RETURNING {', '.join('r.' + c for c in _SELECT_COLUMNS.split(', '))}
                    """,
                    params)
                row = cur.fetchone()
        return ProcessingJob.from_row(dict(row)) if row is not None else None

    def heartbeat(self, *, job_id: int, worker_id: str, current_stage: str | None = None,
                  progress_percent: int | None = None) -> None:
        """更新執行中工作的 heartbeat、階段與進度（只認持鎖 worker）。"""
        extra: dict[str, Any] = {}
        if current_stage is not None:
            extra["current_stage"] = current_stage
        if progress_percent is not None:
            extra["progress_percent"] = progress_percent
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.workflow_runs
                    SET worker_state_json = worker_state_json || %s::jsonb
                        || jsonb_build_object('heartbeat_at', to_jsonb(now()))
                    WHERE run_id = %s AND worker_state_json->>'locked_by' = %s AND status = 'running'
                    """,
                    (Jsonb(extra), job_id, worker_id))

    def complete_job(self, *, job_id: int, worker_id: str, result_json: dict[str, Any]) -> None:
        """保存結果（workflow_outputs 版本化）並標記成功；狀態被改動時 raise。"""
        # 先取 run_type 以組 output_type；再寫結果，最後守鎖更新狀態。
        with psycopg.connect(**get_connection_kwargs()) as conn:
            row = conn.execute(
                "SELECT run_type FROM app_layer.workflow_runs WHERE run_id = %s", (job_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError(f"job {job_id} not found")
        PostgresWorkflowOutputsRepository().append_output(
            job_id, _result_output_type(str(row[0])), result_json)
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.workflow_runs
                    SET status = 'succeeded',
                        worker_state_json = worker_state_json || jsonb_build_object(
                            'progress_percent', 100, 'current_stage', 'completed',
                            'error_message', NULL, 'heartbeat_at', to_jsonb(now()),
                            'finished_at', to_jsonb(now()))
                    WHERE run_id = %s AND worker_state_json->>'locked_by' = %s AND status = 'running'
                    """,
                    (job_id, worker_id))
                if cur.rowcount != 1:
                    raise RuntimeError(f"job {job_id} was not completed; state changed")

    def fail_job(self, *, job_id: int, worker_id: str, error_message: str,
                 current_stage: str = "failed") -> None:
        """標記失敗並保存可讀錯誤訊息（只認持鎖 worker）。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.workflow_runs
                    SET status = 'failed',
                        worker_state_json = worker_state_json || jsonb_build_object(
                            'current_stage', %s::text, 'error_message', %s::text,
                            'heartbeat_at', to_jsonb(now()), 'finished_at', to_jsonb(now()))
                    WHERE run_id = %s AND worker_state_json->>'locked_by' = %s AND status = 'running'
                    """,
                    (current_stage, error_message[:4000], job_id, worker_id))

    def cancel_job(self, *, job_id: int, worker_id: str, error_message: str) -> None:
        """把已被外部取消的工作收斂成 cancelled 終態（worker 端）。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.workflow_runs
                    SET status = 'cancelled',
                        worker_state_json = worker_state_json || jsonb_build_object(
                            'current_stage', 'cancelled', 'error_message', %s::text,
                            'heartbeat_at', to_jsonb(now()), 'finished_at', to_jsonb(now()))
                    WHERE run_id = %s AND worker_state_json->>'locked_by' = %s
                      AND status IN ('running', 'cancelled')
                    """,
                    (error_message[:4000], job_id, worker_id))

    def is_cancelled(self, *, job_id: int) -> bool:
        """確認工作是否已被 backend 或使用者標記為 cancelled。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM app_layer.workflow_runs WHERE run_id = %s", (job_id,))
                row = cur.fetchone()
        return row is not None and row[0] == "cancelled"

    def requeue_stale_jobs(self, *, stale_after_seconds: int) -> dict[str, int]:
        """回收 heartbeat 逾時的 running 工作：達嘗試上限標 failed，否則退回 queued。"""
        stale_interval = timedelta(seconds=stale_after_seconds)
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.workflow_runs
                    SET status = 'failed',
                        worker_state_json = worker_state_json || jsonb_build_object(
                            'current_stage', 'stale_failed',
                            'error_message', 'worker heartbeat timed out',
                            'locked_by', NULL, 'locked_at', NULL, 'finished_at', to_jsonb(now()))
                    WHERE status = 'running'
                      AND (worker_state_json->>'heartbeat_at')::timestamptz < now() - %s::interval
                      AND COALESCE((worker_state_json->>'attempt_count')::int, 0)
                          >= COALESCE((worker_state_json->>'max_attempts')::int, 3)
                    """,
                    (stale_interval,))
                failed_count = cur.rowcount
                cur.execute(
                    """
                    UPDATE app_layer.workflow_runs
                    SET status = 'queued',
                        worker_state_json = worker_state_json || jsonb_build_object(
                            'current_stage', 'requeued',
                            'locked_by', NULL, 'locked_at', NULL, 'heartbeat_at', NULL)
                    WHERE status = 'running'
                      AND (worker_state_json->>'heartbeat_at')::timestamptz < now() - %s::interval
                      AND COALESCE((worker_state_json->>'attempt_count')::int, 0)
                          < COALESCE((worker_state_json->>'max_attempts')::int, 3)
                    """,
                    (stale_interval,))
                requeued_count = cur.rowcount
        return {"failed_count": int(failed_count), "requeued_count": int(requeued_count)}
