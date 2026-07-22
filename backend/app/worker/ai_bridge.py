"""Host-side AI bridge runner。

這個模組是正式的 AI CLI 橋接器入口：它不放進一般 backend/worker 容器的必要
路徑，而是跑在「有 Claude CLI / OpenCode CLI 的受控主機」上，透過同一個
workflow_runs queue claim AI 任務，執行既有 AI handler，再把結果寫回
workflow_outputs。

典型開發環境：
    uv run python -m backend.app.worker.ai_bridge run-once
    uv run python -m backend.app.worker.ai_bridge serve

正式部署時可放在同 server 或內網機器，靠 .env 的 PGHOST/PGPORT/PGDATABASE 等
變數連資料庫；不綁本機路徑、不要求 Lightning 容器能看到使用者電腦上的 CLI。
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.app.db import job_repository

from .queue_client import WorkerQueueClient
from .runner import execute_job


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_JOB_TYPES: tuple[str, ...] = ("ai:narrative",)
SMOKE_VERSION = "ai_bridge_db_smoke_v1"


def load_local_env() -> None:
    """載入專案 .env；正式環境可用系統 env 覆蓋，不把部署位置寫死。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def default_bridge_id() -> str:
    """建立可追蹤的 bridge id，會寫進 workflow_runs 的 locked_by。"""
    return os.getenv("AI_BRIDGE_ID") or f"ai-bridge-{socket.gethostname()}-{os.getpid()}"


def run_once(
    *,
    worker_id: str,
    stale_after_seconds: int,
    store: WorkerQueueClient | None = None,
) -> dict[str, Any]:
    """claim 並執行一筆 AI job；沒有 AI job 時回 idle。

    store 可注入是為了單元測試；正式執行使用 WorkerQueueClient 連資料庫。
    """
    queue = store if store is not None else WorkerQueueClient()
    stale = queue.requeue_stale_jobs(stale_after_seconds=stale_after_seconds)
    job = queue.claim_next_job(worker_id=worker_id, job_types=AI_JOB_TYPES)
    if job is None:
        return {"status": "idle", "stale": stale}
    LOGGER.info("AI job claimed: id=%s type=%s", job.job_id, job.job_type)
    return execute_job(job, worker_id=worker_id, store=queue)


def run_smoke(*, worker_id: str, store: WorkerQueueClient | None = None) -> dict[str, Any]:
    """執行受控 DB smoke，不呼叫外部 CLI。

    smoke 只驗 workflow_runs / workflow_outputs 的正式橋接路徑：建立專屬 AI job、
    exact-claim 該 job、heartbeat、complete。它不會 claim 其他 queued AI 任務，
    也不需要 report artifact 或 Claude CLI 登入狀態。
    """
    queue = store if store is not None else WorkerQueueClient()
    requested_at = datetime.now(UTC).isoformat()
    payload = {
        "smoke": True,
        "smoke_version": SMOKE_VERSION,
        "requested_at": requested_at,
        "requested_by": worker_id,
    }
    job = job_repository.create_job(
        "ai:narrative",
        payload,
        idempotency_key=f"ai-bridge-smoke-{requested_at}",
        max_attempts=1,
    )
    claimed = queue.claim_job_by_id(
        job_id=job.job_id,
        worker_id=worker_id,
        job_types=AI_JOB_TYPES,
    )
    if claimed is None:
        raise RuntimeError(f"AI bridge smoke job {job.job_id} was not claimable")
    queue.heartbeat(
        job_id=claimed.job_id,
        worker_id=worker_id,
        current_stage="bridge_smoke_completing",
        progress_percent=90,
    )
    result = {
        "smoke": True,
        "smoke_version": SMOKE_VERSION,
        "job_id": claimed.job_id,
        "job_type": claimed.job_type,
        "worker_id": worker_id,
        "requested_at": requested_at,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    queue.complete_job(job_id=claimed.job_id, worker_id=worker_id, result_json=result)
    return {"status": "succeeded", "job_id": claimed.job_id, "result": result}


def serve(*, worker_id: str, poll_seconds: float, stale_after_seconds: int) -> None:
    """常駐輪詢 AI queue；正式 bridge 由 process manager 或服務平台管理。"""
    LOGGER.info("AI bridge started: worker_id=%s job_types=%s", worker_id, AI_JOB_TYPES)
    while True:
        result = run_once(worker_id=worker_id, stale_after_seconds=stale_after_seconds)
        if result["status"] == "idle":
            time.sleep(poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    """建立 host-side AI bridge CLI 參數。"""
    parser = argparse.ArgumentParser(description="Run host-side patent AI bridge.")
    parser.add_argument("command", choices=("serve", "run-once", "smoke"))
    parser.add_argument("--worker-id", default=default_bridge_id())
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--stale-after-seconds", type=int, default=1800)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> None:
    """CLI 入口：單步驗收或常駐服務。"""
    load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()))
    if args.command == "smoke":
        result = run_smoke(worker_id=args.worker_id)
        LOGGER.info("ai-bridge smoke result: %s", result)
        return
    if args.command == "run-once":
        result = run_once(
            worker_id=args.worker_id,
            stale_after_seconds=args.stale_after_seconds,
        )
        LOGGER.info("ai-bridge run-once result: %s", result)
        return
    serve(
        worker_id=args.worker_id,
        poll_seconds=args.poll_seconds,
        stale_after_seconds=args.stale_after_seconds,
    )


if __name__ == "__main__":
    main()
