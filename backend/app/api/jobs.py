"""Job 狀態 API。

提供 liveness、readiness 與 job 查詢。readiness 會分辨：
資料庫連線或連線設定錯誤，以及 DB 可連但 worker heartbeat 查詢失敗。
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException

from backend.app import settings
from backend.app.db import job_repository
from backend.app.db.connection import get_connection_kwargs


router = APIRouter(tags=["jobs"])


def job_to_dict(job: job_repository.ProcessingJob) -> dict[str, Any]:
    """把 ProcessingJob 轉成 API 回傳格式，供 jobs/clustering/reports 共用。"""
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "workspace_id": job.workspace_id,
        "payload": job.payload_json,
        "result": job.result_json,
        "progress_percent": job.progress_percent,
        "current_stage": job.current_stage,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        # 失敗／逾時原因；前端失敗卡點開讀此欄，未失敗為 None
        "error_message": job.error_message,
    }


@router.get("/health")
def health() -> dict[str, str]:
    """liveness：只確認應用程式進程可回應，不碰 DB。"""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, Any]:
    """readiness：確認 DB 可連，並檢查 running jobs 的 heartbeat 狀態。"""
    try:
        kwargs = get_connection_kwargs()
        conn = psycopg.connect(**kwargs, connect_timeout=3)
    except Exception as exc:  # noqa: BLE001 - 連線參數或連線失敗都歸類成 DB not ready
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "database": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            },
        ) from exc

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                # 0021：佇列遷至 workflow_runs，worker 簿記（含 heartbeat_at）收在 worker_state_json
                cur.execute(
                    """
                    SELECT
                        count(*) AS running,
                        count(*) FILTER (
                            WHERE (worker_state_json->>'heartbeat_at')::timestamptz
                                  < now() - make_interval(secs => %s)
                        ) AS stale,
                        EXTRACT(EPOCH FROM (
                            now() - max((worker_state_json->>'heartbeat_at')::timestamptz)
                        ))::int AS latest_age
                    FROM app_layer.workflow_runs
                    WHERE status = 'running'
                    """,
                    (settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS,),
                )
                row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - DB 可連但 readiness 查詢或 schema 有問題
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "database": {"ok": True, "port": kwargs.get("port")},
                "worker": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            },
        ) from exc

    running, stale, latest_age = int(row[0]), int(row[1]), row[2]
    worker = {
        "running_jobs": running,
        "stale_running_jobs": stale,
        "latest_heartbeat_age_seconds": int(latest_age) if latest_age is not None else None,
        "heartbeat_timeout_seconds": settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS,
        "healthy": running == 0 or stale < running,
    }
    return {
        "status": "ready",
        "database": {"ok": True, "port": kwargs.get("port")},
        "worker": worker,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    """依 job_id 查詢單一工作；不存在時回 404。"""
    job = job_repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    body = job_to_dict(job)
    # 0021 後 job 結果主要存 workflow_outputs；遷移或舊任務沒有 output 時保留 row result。
    output_result = job_repository.fetch_job_result(job.job_id, job.job_type)
    body["result"] = output_result if output_result is not None else job.result_json
    return body
