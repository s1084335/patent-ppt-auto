"""Job 相關 API：health、ready 與單筆工作查詢。

health＝liveness（程式活著即回，不碰 DB）；ready＝readiness（檢查 DB 可連並
回報 worker 心跳新鮮度，DB 不通回 503）；GET /jobs/{id} 由 job_repository 讀狀態。
建立工作的 endpoint 在 E3（clustering／reports）另加。
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
    """把 ProcessingJob 轉成 API 回傳格式（jobs/clustering/reports 共用）。"""
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
    }


@router.get("/health")
def health() -> dict[str, str]:
    """liveness：程式存活即回 ok，不連 DB。"""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, Any]:
    """readiness：DB 可連才算 ready（不通回 503），並附 worker 心跳新鮮度。

    worker 健康以「running job 的心跳」推斷：無 running job 時無法確認 worker
    是否在跑（idle），但只要 DB 通、backend 就能收工作，故不因此判 not_ready。
    """
    kwargs = get_connection_kwargs()
    worker: dict[str, Any]
    try:
        with psycopg.connect(**kwargs, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.execute(
                    """
                    SELECT
                        count(*) AS running,
                        count(*) FILTER (
                            WHERE heartbeat_at < now() - make_interval(secs => %s)
                        ) AS stale,
                        EXTRACT(EPOCH FROM (now() - max(heartbeat_at)))::int AS latest_age
                    FROM app_layer.processing_jobs
                    WHERE status = 'running'
                    """,
                    (settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS,),
                )
                row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - DB 不通即 not ready
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "database": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            },
        ) from exc

    running, stale, latest_age = int(row[0]), int(row[1]), row[2]
    worker = {
        "running_jobs": running,
        "stale_running_jobs": stale,
        "latest_heartbeat_age_seconds": int(latest_age) if latest_age is not None else None,
        "heartbeat_timeout_seconds": settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS,
        # 有 running job 但全部心跳逾時＝worker 可能失聯。
        "healthy": running == 0 or stale < running,
    }
    return {
        "status": "ready",
        "database": {"ok": True, "port": kwargs.get("port")},
        "worker": worker,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    """查詢單筆工作的狀態、進度、階段與結果；不存在回 404。"""
    job = job_repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job_to_dict(job)
