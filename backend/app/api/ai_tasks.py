"""AI 任務 API：**Web 前端建立 AI 任務的入口**（原 `companion.py`）。

⚠ 命名澄清：這裡的「AI 任務」指瀏覽器端使用者按下按鈕後，由前端呼叫本 API
建立 `ai:narrative` 之類的 job 並輪詢結果。**這不是**架構定案裡的
「本機 Patent Companion」——後者是使用者電腦上的常駐程式，用裝置 token 主動
向中央取任務、驅動本機 Claude Code CLI，屬裝置側取任務通道，目前**尚未實作**。
兩者方向相反（前端 push 建任務 vs 裝置 pull 取任務），不可混用同一組端點；
本檔改名即為消除原 `companion.py` 造成的誤導。

本檔同時整併原 `events.py` 的 `POST /ai-tasks`（語意重疊的第二個建任務端點），
因此 POST 同時接受兩種 body 形狀，見 `CreateAiTaskRequest`。

所有端點都要求 `Authorization: Bearer <PATENT_API_TOKEN>`，見 `_auth.py`。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from backend.app.api._auth import require_api_token
from backend.app.db import job_repository as jr
from backend.app.worker.ai_bridge import AI_JOB_TYPES, CLI_BINARIES


router = APIRouter(
    prefix="/ai-tasks",
    tags=["ai-tasks"],
    dependencies=[Depends(require_api_token)],
)

SUPPORTED_CLI_KINDS = tuple(CLI_BINARIES.keys())


class CreateAiTaskRequest(BaseModel):
    """前端建立 AI 任務的請求格式。

    兩種 body 形狀擇一（整併自原 companion 與原 events 兩個入口）：
    - 具名欄位：`{"cli_kind": ..., "instruction": ..., "workspace_id": ...}`
    - 泛型形狀：`{"task_type": "ai:narrative", "params": {...}}`
    兩者最終都建立同一種 job，避免留下兩個語意重疊的端點。
    """

    task_type: str = "ai:narrative"
    params: dict[str, Any] = Field(default_factory=dict)

    workspace_id: int | None = Field(default=None, ge=1)
    cli_kind: Literal["claude", "opencode"] = "claude"
    based_on_version: str | None = None
    instruction: str | None = None
    model: str | None = None
    cli_timeout_seconds: float | None = Field(default=None, gt=0)
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _check_task_type(self) -> "CreateAiTaskRequest":
        """task_type 必須是 AI bridge 認得的型別，不能拖到 worker 才失敗。"""
        if self.task_type not in AI_JOB_TYPES:
            raise ValueError(f"unsupported task_type: {self.task_type}")
        return self

    def to_payload(self) -> dict[str, Any]:
        """組出交給 worker 的 payload：泛型 params 打底，具名欄位覆蓋其上。"""
        payload: dict[str, Any] = dict(self.params)
        named = self.model_dump(
            exclude={"task_type", "params", "workspace_id", "idempotency_key"},
            exclude_none=True,
        )
        payload.update(named)
        return payload


class AiTaskCreatedResponse(BaseModel):
    """建立 AI 任務後給前端輪詢使用的最小回應。"""

    run_id: int
    job_type: str
    status: str
    poll_url: str


class AiTaskResponse(BaseModel):
    """AI 任務查詢回應，合併佇列狀態與 AI 輸出結果。"""

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
def ai_tasks_status() -> dict[str, Any]:
    """回傳前端 AI 任務入口與 AI bridge 的靜態能力邊界。"""
    return {
        "status": "ready",
        "ai_bridge": {
            "supported_job_types": list(AI_JOB_TYPES),
            "supported_cli_kinds": list(SUPPORTED_CLI_KINDS),
            "normal_worker_consumes_ai_jobs": False,
        },
    }


@router.post("", status_code=201, response_model=AiTaskCreatedResponse)
def create_ai_task(body: CreateAiTaskRequest) -> AiTaskCreatedResponse:
    """建立 AI 任務（目前為 ai:narrative），交由獨立 AI bridge 執行。"""
    try:
        job = jr.create_job(
            body.task_type,
            payload=body.to_payload(),
            workspace_id=body.workspace_id,
            idempotency_key=body.idempotency_key,
            max_attempts=1,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AiTaskCreatedResponse(
        run_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        poll_url=f"/api/v1/jobs/{job.job_id}",
    )


@router.get("/{run_id}", response_model=AiTaskResponse)
def get_ai_task(run_id: int) -> AiTaskResponse:
    """查詢單筆 AI 任務，結果從 workflow_outputs 讀最新版本。"""
    job = jr.get_job(run_id)
    if job is None or job.job_type not in AI_JOB_TYPES:
        raise HTTPException(status_code=404, detail=f"ai task {run_id} not found")

    return AiTaskResponse(
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
