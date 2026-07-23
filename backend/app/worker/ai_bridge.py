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
import shutil
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.app.db import job_repository

from .job_context import JobCancelledError, JobContext
from .queue_client import WorkerQueueClient


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# AI job 集合的唯一事實來源在 job_repository；bridge 與一般 worker 由同一份常數推導分工，
# 不再各自維護字面值（以往兩處字面值重複，新增 AI 任務時容易漏改而讓一般 worker 誤領）。
AI_JOB_TYPES: tuple[str, ...] = tuple(sorted(job_repository.AI_JOB_TYPES))
SMOKE_VERSION = "ai_bridge_db_smoke_v1"
CLI_BINARIES: dict[str, str] = {
    "claude": "claude",
    "opencode": "opencode",
}


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
    return execute_ai_job(job, worker_id=worker_id, store=queue)


def _run_ai_narrative_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行 AI 敘事任務；延遲載入 handler，避免 bridge 啟動時拉進一般 worker 依賴。"""
    from .handlers import handle_ai_narrative

    return handle_ai_narrative(payload, context)


def _run_ai_topic_label_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行主題標籤／摘要任務：驅動 headless CLI 讀代表性專利文檔後命名。

    payload：workspace_id、source_field（必要）；topic_keys（可選，不給＝全部 active 主題）、
    cli_kind／model／cli_timeout_seconds（沿用 ai:narrative 的 payload 慣例）。

    🔴 keywords 不會出現在 payload 內：CLI 看得到的內容由 ai_topic_label_runner 組裝，
    只含代表性專利文檔與必要 metadata（使用者定案）。延遲載入 runner，理由同上。

    階段映射（AI 任務無內部百分比，用階段緩進）：開始 15 →（runner 內 30→85）→ 回填 90 → 100。
    """
    from . import ai_topic_label_runner

    context.heartbeat("開始 AI 主題標籤", 15)
    workspace_id = payload.get("workspace_id")
    if workspace_id is None:
        raise ValueError("ai:topic_label payload requires workspace_id")
    source_field = payload.get("source_field")
    if not source_field:
        raise ValueError("ai:topic_label payload requires source_field")

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的 CLI 執行進度轉成 worker heartbeat（繁中階段文字）。"""
        context.heartbeat("CLI 主題命名執行中", percent)

    result = ai_topic_label_runner.run_topic_label(
        workspace_id=int(workspace_id),
        source_field=str(source_field),
        topic_keys=payload.get("topic_keys") or None,
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_topic_label_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        progress=_progress,
    )
    context.heartbeat("標籤已回存", 90)
    context.heartbeat("完成", 100)
    return result


def _run_ai_patent_note_job(payload: dict[str, Any], context: JobContext) -> dict[str, Any]:
    """執行文獻備註任務：驅動 headless CLI 讀專利獨立項摘要成備註後回填。

    payload：workspace_id（可選，不給＝全庫）、char_budget／limit／skip_existing（可選）、
    cli_kind／model／cli_timeout_seconds（沿用 ai:narrative 的 payload 慣例）。

    進度：runner 內部每批回報一次（5→95，帶「第 n/N 批」文字），直接轉成 heartbeat；
    1900 件會分成數十批，使用者看得到 0→100 推進，不是無限 spinner。
    """
    from . import ai_patent_note_runner

    context.heartbeat("開始產生文獻備註", 1)

    def _progress(stage: str, percent: int) -> None:
        """把 runner 的分批進度轉成 worker heartbeat（繁中階段文字直接沿用）。"""
        context.heartbeat(stage, percent)

    workspace_id = payload.get("workspace_id")
    return ai_patent_note_runner.run_patent_note(
        workspace_id=int(workspace_id) if workspace_id is not None else None,
        cli_kind=str(payload.get("cli_kind") or "claude"),
        model=payload.get("model") or None,
        # _cli_runner 供測試／Companion 注入假或替代執行器；正式跑真實 subprocess。
        cli_runner=payload.get("_cli_runner"),
        char_budget=int(
            payload.get("char_budget") or ai_patent_note_runner.DEFAULT_CHAR_BUDGET
        ),
        skip_existing=bool(payload.get("skip_existing", True)),
        limit=int(payload["limit"]) if payload.get("limit") else None,
        timeout_seconds=float(
            payload.get("cli_timeout_seconds")
            or ai_patent_note_runner.DEFAULT_CLI_TIMEOUT_SECONDS
        ),
        progress=_progress,
    )


# job_type → 執行函式。值存「函式名」而非函式物件，讓 execute_ai_job 在呼叫當下才解析到
# 模組屬性——測試以 mock.patch.object 換掉 _run_ai_* 時才會生效（存物件會綁死原函式）。
_AI_JOB_RUNNERS: dict[str, str] = {
    "ai:narrative": "_run_ai_narrative_job",
    "ai:topic_label": "_run_ai_topic_label_job",
    "ai:patent_note": "_run_ai_patent_note_job",
}


class _LateBoundHandlers:
    """依 job_type 取回當下模組屬性的小查表器（保持 execute_ai_job 讀起來像 dict）。"""

    def get(self, job_type: str):
        """回傳該 job_type 的執行函式；未支援時回 None。"""
        name = _AI_JOB_RUNNERS.get(job_type)
        return globals().get(name) if name else None


_AI_JOB_HANDLERS = _LateBoundHandlers()


def execute_ai_job(job: job_repository.ProcessingJob, *, worker_id: str, store: WorkerQueueClient) -> dict[str, Any]:
    """只執行 AI bridge 支援的 job，成功、失敗、取消都回寫 workflow queue。"""
    context = JobContext(job=job, worker_id=worker_id, store=store)
    try:
        handler = _AI_JOB_HANDLERS.get(job.job_type)
        if handler is None:
            raise ValueError(f"unsupported AI bridge job_type: {job.job_type}")
        context.heartbeat("running", 1)
        result = handler(job.payload_json, context)
        store.complete_job(job_id=job.job_id, worker_id=worker_id, result_json=result)
        LOGGER.info("AI job succeeded: id=%s type=%s", job.job_id, job.job_type)
        return {"job_id": job.job_id, "status": "succeeded", "result": result}
    except JobCancelledError as exc:
        LOGGER.warning("AI job cancelled: id=%s error=%s", job.job_id, exc)
        store.cancel_job(job_id=job.job_id, worker_id=worker_id, error_message=str(exc))
        return {"job_id": job.job_id, "status": "cancelled", "error": str(exc)}
    except Exception as exc:
        LOGGER.exception("AI job failed: id=%s type=%s", job.job_id, job.job_type)
        store.fail_job(
            job_id=job.job_id,
            worker_id=worker_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return {"job_id": job.job_id, "status": "failed", "error": str(exc)}


def _db_check() -> dict[str, Any]:
    """用唯讀列表查詢確認 workflow queue 可連線；doctor 不建立任何 job。"""
    try:
        job_repository.list_jobs(limit=1)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True}


def _cli_check(cli_kind: str) -> dict[str, Any]:
    """確認指定 headless CLI 是否存在於 PATH；不送 prompt、不消耗 LLM。"""
    binary = CLI_BINARIES.get(cli_kind)
    if binary is None:
        return {"ok": False, "binary": None, "error": f"unsupported cli_kind: {cli_kind}"}
    path = shutil.which(binary)
    return {"ok": path is not None, "binary": binary, "path": path}


def run_doctor(*, cli_kind: str = "claude") -> dict[str, Any]:
    """正式部署前診斷 DB queue 與本機 AI CLI 條件。"""
    return {"database": _db_check(), "cli": _cli_check(cli_kind)}


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
    parser.add_argument("command", choices=("serve", "run-once", "smoke", "doctor"))
    parser.add_argument("--worker-id", default=default_bridge_id())
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--stale-after-seconds", type=int, default=1800)
    parser.add_argument("--cli-kind", choices=tuple(CLI_BINARIES), default="claude")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> None:
    """CLI 入口：單步驗收或常駐服務。"""
    load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()))
    if args.command == "doctor":
        result = run_doctor(cli_kind=args.cli_kind)
        LOGGER.info("ai-bridge doctor result: %s", result)
        return
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
