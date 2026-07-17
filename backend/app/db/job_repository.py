"""app_layer.processing_jobs 的單一 DB 存取層（backend＋worker 共用）。

分工定案（2026-07-17，方案 A）：processing_jobs 的所有 DB 存取集中在本檔，
由 Claude 維護，backend 與 worker 都 import 這裡，避免兩份實作漂移。

- backend 端（建立/查詢/取消）：module-level 函式 create_job／get_job／
  list_jobs／cancel_job。
- worker 端（領取/心跳/完成/失敗/取消收斂/回收）：WorkerQueueClient 類別。
  其邏輯沿用 Codex 已驗證的 worker queue 實作；claim 用 FOR UPDATE SKIP LOCKED，
  與 migration 0012 鎖定的 contract 完全一致。

correctness-critical 的原子領取與逾時回收 SQL 由 schema 擁有者（Claude）維護。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs


# 合法工作類型（DB 不設 job_type check，由此白名單於 backend 建立時驗證）。
JOB_TYPES: frozenset[str] = frozenset(
    {
        "clustering_calibrate",
        "clustering_finalize",
        "clustering_incremental",
        "report_generate",
    }
)

# 終態：不可再被領取或改動。
TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

# 回傳整列的欄位，統一給 ProcessingJob.from_row 使用。
_SELECT_COLUMNS = (
    "job_id, job_type, status, workspace_id, payload_json, result_json, "
    "progress_percent, current_stage, attempt_count, max_attempts"
)


@dataclass(frozen=True)
class ProcessingJob:
    """代表 app_layer.processing_jobs 的單筆工作（backend 與 worker 共用）。"""

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

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ProcessingJob":
        """把 psycopg dict row 轉為內部資料物件。"""
        return cls(
            job_id=int(row["job_id"]),
            job_type=str(row["job_type"]),
            status=str(row["status"]),
            workspace_id=int(row["workspace_id"]) if row["workspace_id"] is not None else None,
            payload_json=dict(row["payload_json"] or {}),
            result_json=dict(row["result_json"]) if row["result_json"] is not None else None,
            progress_percent=int(row["progress_percent"]),
            current_stage=str(row["current_stage"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
        )


# ── backend 端：建立、查詢、取消 ───────────────────────────────


def create_job(
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    workspace_id: int | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> ProcessingJob:
    """建立一筆 queued 工作；帶 idempotency_key 且已存在時回傳既有工作，不重建。

    job_type 必須在 JOB_TYPES 白名單內。payload 存進 payload_json 供 worker
    handler 取用。idempotency 依賴 0012 的部分唯一索引（key 非 NULL 才受約束）。
    """
    if job_type not in JOB_TYPES:
        raise ValueError(f"unsupported job_type: {job_type}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    payload_json = Jsonb(payload or {})
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO app_layer.processing_jobs
                    (job_type, payload_json, workspace_id, idempotency_key, max_attempts)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING {_SELECT_COLUMNS}
                """,
                (job_type, payload_json, workspace_id, idempotency_key, max_attempts),
            )
            row = cur.fetchone()
            if row is None:
                # idempotency 命中：回傳既有工作，不新增第二筆。
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM app_layer.processing_jobs "
                    "WHERE idempotency_key = %s",
                    (idempotency_key,),
                )
                row = cur.fetchone()
        conn.commit()
    return ProcessingJob.from_row(row)


def get_job(job_id: int) -> ProcessingJob | None:
    """依 job_id 取單筆工作；不存在回 None。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM app_layer.processing_jobs WHERE job_id = %s",
                (job_id,),
            )
            row = cur.fetchone()
    return ProcessingJob.from_row(row) if row is not None else None


def list_jobs(
    *,
    workspace_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[ProcessingJob]:
    """列出工作（新到舊），可依 workspace 或狀態過濾。"""
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
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM app_layer.processing_jobs "
                f"{where} ORDER BY created_at DESC, job_id DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
    return [ProcessingJob.from_row(row) for row in rows]


def cancel_job(job_id: int) -> ProcessingJob | None:
    """backend 端請求取消：queued 直接收斂為 cancelled；running 標記 cancelled
    後由 worker 的 is_cancelled 檢查停止。取消即終點，一律寫 finished_at；
    running 中的 worker 因 status 不再是 running 而無法覆寫。已是終態則不動、
    回傳現況。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE app_layer.processing_jobs
                SET status = 'cancelled',
                    current_stage = 'cancelled',
                    finished_at = now()
                WHERE job_id = %s AND status IN ('queued', 'running')
                RETURNING {_SELECT_COLUMNS}
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM app_layer.processing_jobs WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        conn.commit()
    return ProcessingJob.from_row(row) if row is not None else None


# ── worker 端：領取、心跳、完成、失敗、取消收斂、回收 ───────────
# 邏輯沿用 Codex 的 worker queue 實作，集中到本層由 Claude 維護。


class WorkerQueueClient:
    """worker 對 app_layer.processing_jobs 的所有寫入規則。"""

    def claim_next_job(self, *, worker_id: str) -> ProcessingJob | None:
        """用 FOR UPDATE SKIP LOCKED 原子領取下一筆 queued 工作（0012 驗過的 contract）。"""
        with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH next_job AS (
                        SELECT job_id
                        FROM app_layer.processing_jobs
                        WHERE status = 'queued'
                          AND attempt_count < max_attempts
                        ORDER BY created_at, job_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE app_layer.processing_jobs AS jobs
                    SET status = 'running',
                        locked_by = %s,
                        locked_at = now(),
                        heartbeat_at = now(),
                        started_at = COALESCE(started_at, now()),
                        attempt_count = attempt_count + 1,
                        current_stage = 'starting',
                        error_message = NULL
                    FROM next_job
                    WHERE jobs.job_id = next_job.job_id
                    RETURNING jobs.*
                    """,
                    (worker_id,),
                )
                row = cur.fetchone()
        return ProcessingJob.from_row(dict(row)) if row is not None else None

    def heartbeat(
        self,
        *,
        job_id: int,
        worker_id: str,
        current_stage: str | None = None,
        progress_percent: int | None = None,
    ) -> None:
        """更新執行中工作的 heartbeat、階段與進度（只認持鎖 worker）。"""
        assignments = ["heartbeat_at = now()"]
        params: list[Any] = []
        if current_stage is not None:
            assignments.append("current_stage = %s")
            params.append(current_stage)
        if progress_percent is not None:
            assignments.append("progress_percent = %s")
            params.append(progress_percent)
        params.extend([job_id, worker_id])
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE app_layer.processing_jobs
                    SET {", ".join(assignments)}
                    WHERE job_id = %s AND locked_by = %s AND status = 'running'
                    """,
                    params,
                )

    def complete_job(self, *, job_id: int, worker_id: str, result_json: dict[str, Any]) -> None:
        """標記成功並保存 handler 結果；狀態被改動時 raise。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.processing_jobs
                    SET status = 'succeeded', progress_percent = 100,
                        current_stage = 'completed', result_json = %s,
                        error_message = NULL, heartbeat_at = now(),
                        finished_at = now()
                    WHERE job_id = %s AND locked_by = %s AND status = 'running'
                    """,
                    (Jsonb(result_json), job_id, worker_id),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"job {job_id} was not completed; state changed")

    def fail_job(
        self,
        *,
        job_id: int,
        worker_id: str,
        error_message: str,
        current_stage: str = "failed",
    ) -> None:
        """標記失敗並保存可讀錯誤訊息（只認持鎖 worker）。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.processing_jobs
                    SET status = 'failed', current_stage = %s, error_message = %s,
                        heartbeat_at = now(), finished_at = now()
                    WHERE job_id = %s AND locked_by = %s AND status = 'running'
                    """,
                    (current_stage, error_message[:4000], job_id, worker_id),
                )

    def cancel_job(self, *, job_id: int, worker_id: str, error_message: str) -> None:
        """把已被外部取消的工作收斂成 cancelled 終態（worker 端）。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.processing_jobs
                    SET status = 'cancelled', current_stage = 'cancelled',
                        error_message = %s, heartbeat_at = now(),
                        finished_at = now()
                    WHERE job_id = %s AND locked_by = %s
                      AND status IN ('running', 'cancelled')
                    """,
                    (error_message[:4000], job_id, worker_id),
                )

    def is_cancelled(self, *, job_id: int) -> bool:
        """確認工作是否已被 backend 或使用者標記為 cancelled。"""
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM app_layer.processing_jobs WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        return row is not None and row[0] == "cancelled"

    def requeue_stale_jobs(self, *, stale_after_seconds: int) -> dict[str, int]:
        """回收 heartbeat 逾時的 running 工作：達嘗試上限標 failed，否則退回 queued。"""
        stale_interval = timedelta(seconds=stale_after_seconds)
        with psycopg.connect(**get_connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_layer.processing_jobs
                    SET status = 'failed', current_stage = 'stale_failed',
                        error_message = 'worker heartbeat timed out',
                        locked_by = NULL, locked_at = NULL,
                        finished_at = now()
                    WHERE status = 'running'
                      AND heartbeat_at < now() - %s::interval
                      AND attempt_count >= max_attempts
                    """,
                    (stale_interval,),
                )
                failed_count = cur.rowcount
                cur.execute(
                    """
                    UPDATE app_layer.processing_jobs
                    SET status = 'queued', current_stage = 'requeued',
                        locked_by = NULL, locked_at = NULL, heartbeat_at = NULL
                    WHERE status = 'running'
                      AND heartbeat_at < now() - %s::interval
                      AND attempt_count < max_attempts
                    """,
                    (stale_interval,),
                )
                requeued_count = cur.rowcount
        return {"failed_count": int(failed_count), "requeued_count": int(requeued_count)}
