"""純 Python worker 入口。

常駐模式：
    uv run python -m backend.app.worker.runner serve

單次模式：
    uv run python -m backend.app.worker.runner run-once
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import socket
import time
from typing import Any

from dotenv import load_dotenv

from backend.app.db.job_repository import JOB_TYPES

from .handlers import dispatch_job
from .job_context import JobCancelledError, JobContext
from .queue_client import ProcessingJob, WorkerQueueClient


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_JOB_TYPES: tuple[str, ...] = ("ai:narrative",)
DEFAULT_WORKER_JOB_TYPES: tuple[str, ...] = tuple(sorted(JOB_TYPES - set(AI_JOB_TYPES)))


def load_local_env() -> None:
    """本機開發時讀取專案 .env；容器正式環境仍以既有環境變數為準。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def default_worker_id() -> str:
    """產生可辨識的 worker id，方便 DB 與 stdout log 追蹤。"""
    return os.getenv("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"


def execute_job(job: ProcessingJob, *, worker_id: str, store: WorkerQueueClient) -> dict[str, Any]:
    """執行單筆已領取工作，並由 runner 統一寫回成功或失敗狀態。"""
    context = JobContext(job=job, worker_id=worker_id, store=store)
    try:
        context.heartbeat("running", 1)
        result = dispatch_job(job.payload_json, context)
        store.complete_job(job_id=job.job_id, worker_id=worker_id, result_json=result)
        LOGGER.info("job succeeded: id=%s type=%s", job.job_id, job.job_type)
        return {"job_id": job.job_id, "status": "succeeded", "result": result}
    except JobCancelledError as exc:
        LOGGER.warning("job cancelled: id=%s error=%s", job.job_id, exc)
        store.cancel_job(
            job_id=job.job_id,
            worker_id=worker_id,
            error_message=str(exc),
        )
        return {"job_id": job.job_id, "status": "cancelled", "error": str(exc)}
    except Exception as exc:
        LOGGER.exception("job failed: id=%s type=%s", job.job_id, job.job_type)
        store.fail_job(
            job_id=job.job_id,
            worker_id=worker_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return {"job_id": job.job_id, "status": "failed", "error": str(exc)}


def run_once(*, worker_id: str, stale_after_seconds: int) -> dict[str, Any]:
    """領取並執行一筆工作；沒有 queued 工作時直接回傳 idle。"""
    store = WorkerQueueClient()
    stale = store.requeue_stale_jobs(stale_after_seconds=stale_after_seconds)
    job = store.claim_next_job(worker_id=worker_id, job_types=DEFAULT_WORKER_JOB_TYPES)
    if job is None:
        return {"status": "idle", "stale": stale}
    LOGGER.info("job claimed: id=%s type=%s", job.job_id, job.job_type)
    return execute_job(job, worker_id=worker_id, store=store)


def serve(*, worker_id: str, poll_seconds: float, stale_after_seconds: int) -> None:
    """常駐輪詢 app_layer.workflow_runs 佇列，初期固定單程序單工作執行。"""
    LOGGER.info("worker started: worker_id=%s", worker_id)
    while True:
        result = run_once(worker_id=worker_id, stale_after_seconds=stale_after_seconds)
        if result["status"] == "idle":
            time.sleep(poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    """建立 worker CLI 參數解析器。"""
    parser = argparse.ArgumentParser(description="Run patent backend worker.")
    parser.add_argument("command", choices=("serve", "run-once"))
    parser.add_argument("--worker-id", default=default_worker_id())
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--stale-after-seconds", type=int, default=1800)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> None:
    """CLI 入口，依 command 執行單次或常駐 worker。"""
    load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()))
    if args.command == "run-once":
        result = run_once(worker_id=args.worker_id, stale_after_seconds=args.stale_after_seconds)
        LOGGER.info("run-once result: %s", result)
        return
    serve(
        worker_id=args.worker_id,
        poll_seconds=args.poll_seconds,
        stale_after_seconds=args.stale_after_seconds,
    )


if __name__ == "__main__":
    main()
