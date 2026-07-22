"""Companion API：Web 前端與 AI bridge 之間的薄契約層。

Companion 只負責讓前端建立 AI 任務、知道如何輪詢結果，以及看見正式
AI bridge 邊界；真正執行 Claude/OpenCode CLI 的責任仍在獨立 bridge。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.db import job_repository as jr
from backend.app.worker.ai_bridge import AI_JOB_TYPES, CLI_BINARIES


router = APIRouter(prefix="/companion", tags=["companion"])

SUPPORTED_CLI_KINDS = tuple(CLI_BINARIES.keys())


class NarrativeTaskRequest(BaseModel):
    """前端建立報表敘事 AI 任務的請求格式。"""

    workspace_id: int | None = Field(default=None, ge=1)
    cli_kind: Literal["claude", "opencode"] = "claude"
    based_on_version: str | None = None
    instruction: str | None = None
    model: str | None = None
    cli_timeout_seconds: float | None = Field(default=None, gt=0)
    idempotency_key: str | None = None


class NarrativeTaskResponse(BaseModel):
    """建立 AI 任務後給前端輪詢使用的最小回應。"""

    run_id: int
    job_type: str
    status: str
    poll_url: str


class CompanionTaskResponse(BaseModel):
    """Companion 任務查詢回應，合併佇列狀態與 AI 輸出結果。"""

    run_id: int
    job_type: str
    status: str
    workspace_id: int | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    progress_percent: int
    current_stage: str
    error_message: str | None


@router.get("/status")
def companion_status() -> dict[str, Any]:
    """回傳 Companion 與 AI bridge 的靜態能力邊界。"""
    return {
        "status": "ready",
        "ai_bridge": {
            "supported_job_types": list(AI_JOB_TYPES),
            "supported_cli_kinds": list(SUPPORTED_CLI_KINDS),
            "normal_worker_consumes_ai_jobs": False,
        },
    }


@router.post("/narrative-tasks", status_code=201, response_model=NarrativeTaskResponse)
def create_narrative_task(body: NarrativeTaskRequest) -> NarrativeTaskResponse:
    """建立 ai:narrative 工作，交由獨立 AI bridge 執行。"""
    payload = body.model_dump(
        exclude={"workspace_id", "idempotency_key"},
        exclude_none=True,
    )
    try:
        job = jr.create_job(
            "ai:narrative",
            payload=payload,
            workspace_id=body.workspace_id,
            idempotency_key=body.idempotency_key,
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return NarrativeTaskResponse(
        run_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        poll_url=f"/api/v1/jobs/{job.job_id}",
    )


@router.get("/tasks/{run_id}", response_model=CompanionTaskResponse)
def get_companion_task(run_id: int) -> CompanionTaskResponse:
    """查詢單筆 Companion AI 任務，結果從 workflow_outputs 讀最新版本。"""
    job = jr.get_job(run_id)
    if job is None or job.job_type not in AI_JOB_TYPES:
        raise HTTPException(status_code=404, detail=f"companion task {run_id} not found")

    return CompanionTaskResponse(
        run_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        workspace_id=job.workspace_id,
        payload=job.payload_json,
        result=jr.fetch_job_result(job.job_id, job.job_type),
        progress_percent=job.progress_percent,
        current_stage=job.current_stage,
        error_message=job.error_message,
    )
