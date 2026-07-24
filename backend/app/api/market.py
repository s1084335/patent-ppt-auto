"""Market evidence API。

市場資料線採「AI 找資料、使用者確認、系統入庫」：API 只負責產生 research
brief、暫存候選 evidence、接受人工確認，正式資料才寫入 `market_evidence`。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.api import market_documents
from backend.app.market.evidence_model import MarketEvidenceError
from backend.app.market import evidence_runs
from backend.app.mcp_server import tools_market


router = APIRouter(tags=["market-evidence"])

# 市場 PDF 上傳線（0034 底層）：掛在已註冊的 market router 下，不必另改 main.py 掛新 router。
router.include_router(market_documents.router)


class MarketEvidenceTaskRequest(BaseModel):
    """前端建立市場資料研究任務時傳入的範圍與 evidence 類型。"""

    scope: str
    targets: list[str] = Field(default_factory=list)
    kinds: list[str] | None = None
    report_version: str | None = None
    workspace_id: int | None = None


class MarketEvidenceCandidatesRequest(BaseModel):
    """Claude CLI 回填候選 evidence 時的暫存請求。"""

    run_id: int
    scope: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    report_version: str | None = None


class MarketEvidenceAcceptRequest(BaseModel):
    """使用者確認候選 evidence 後的正式入庫請求。"""

    candidates: list[dict[str, Any]] = Field(default_factory=list)
    accepted_indexes: list[int] | None = None


@router.post("/market-evidence/tasks")
def prepare_market_evidence_task(body: MarketEvidenceTaskRequest) -> dict[str, Any]:
    """產生 Claude CLI 可直接使用的 market research brief。"""
    try:
        task = tools_market.prepare_market_evidence_task(
            scope=body.scope,
            targets=body.targets,
            kinds=body.kinds,
            report_version=body.report_version,
        )
        run = evidence_runs.create_market_evidence_run(
            task_payload=task,
            workspace_id=body.workspace_id,
        )
        return {
            **task,
            "run_id": run["run_id"],
            "run_type": run["run_type"],
            "task_status": run["status"],
            "workspace_id": run["workspace_id"],
        }
    except (ValueError, MarketEvidenceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/market-evidence/candidates")
def save_market_evidence_candidates(body: MarketEvidenceCandidatesRequest) -> dict[str, Any]:
    """把候選 evidence 寫入 workflow_outputs，等待使用者人工確認。"""
    try:
        return tools_market.save_market_evidence_candidates(
            run_id=body.run_id,
            scope=body.scope,
            candidates=body.candidates,
            report_version=body.report_version,
        )
    except (ValueError, MarketEvidenceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/market-evidence/accept")
def accept_market_evidence_candidates(body: MarketEvidenceAcceptRequest) -> dict[str, Any]:
    """只把使用者接受的候選 evidence 寫入正式 market_evidence。"""
    try:
        return tools_market.accept_market_evidence_candidates(
            candidates=body.candidates,
            accepted_indexes=body.accepted_indexes,
        )
    except (ValueError, MarketEvidenceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-evidence")
def list_market_evidence(
    kind: str | None = None,
    scope: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """依 kind、scope、target 查詢已接受的市場 evidence。"""
    try:
        return tools_market.get_market_evidence(kind=kind, scope=scope, target=target)
    except (ValueError, MarketEvidenceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-evidence/aggregate")
def aggregate_market_evidence(scope: str | None = None) -> dict[str, Any]:
    """輸出報表/PPT 可使用的 market evidence 彙總。"""
    try:
        return tools_market.aggregate_market_evidence(scope=scope)
    except (ValueError, MarketEvidenceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
