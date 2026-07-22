"""分群相關 API：建立 calibrate／finalize／incremental 工作，讀取候選方案。

backend 只建立工作與讀結果，不執行分群（那是 worker）。payload 欄名對齊
worker handlers.py 的期待。source_field 以白名單驗證，未知 workspace 由 FK
擋（轉 404）。候選查詢直接讀 legacy_0021.topic_candidates，不 import 分群
引擎（避免把 BERTopic 等重模組載進 backend）。

schema 落點：0021 併表把 topic_candidates 以 SET SCHEMA 搬到 legacy_0021（欄位不變、
仍為實體表），derived_layer 下已無此表，故候選查詢一律走 legacy_0021.topic_candidates。
run 的 workspace_id/status 在 0021 移到 app_layer.workflow_runs，需經 topic_runs.workflow_run_id join。
專案未設 schema 常數，沿用全庫慣例直接寫字面 schema 前綴（見 refresh_report_* 等）。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.jobs import job_to_dict
from backend.app.clustering.sources import get_source_spec
from backend.app.db import job_repository
from backend.app.db.connection import get_connection_kwargs


router = APIRouter(tags=["clustering"])


class CalibrateRequest(BaseModel):
    """建立候選分群工作。"""

    source_field: str
    idempotency_key: str | None = Field(default=None, max_length=200)


class IncrementalRequest(BaseModel):
    """建立增量分群工作。"""

    source_field: str
    idempotency_key: str | None = Field(default=None, max_length=200)


class FinalizeRequest(BaseModel):
    """依使用者選定候選建立定案工作。"""

    candidate_id: int
    selected_by: str = Field(default="web-user", min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, max_length=200)


def _validate_source_field(source_field: str) -> None:
    """source_field 必須是白名單通道，否則 422。"""
    try:
        get_source_spec(source_field)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _create_clustering_job(
    job_type: str,
    payload: dict[str, Any],
    *,
    workspace_id: int | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """建立工作，未知 workspace 的 FK 違反轉成 404。"""
    try:
        job = job_repository.create_job(
            job_type,
            payload,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
        )
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=404, detail=f"workspace {workspace_id} not found"
        ) from exc
    return job_to_dict(job)


@router.post("/workspaces/{workspace_id}/clustering/calibrate")
def create_calibrate(workspace_id: int, request: CalibrateRequest) -> dict[str, Any]:
    """建立候選分群工作（只掃描候選，不定案）。"""
    _validate_source_field(request.source_field)
    return _create_clustering_job(
        "clustering_calibrate",
        {"workspace_id": workspace_id, "source_field": request.source_field},
        workspace_id=workspace_id,
        idempotency_key=request.idempotency_key,
    )


@router.post("/workspaces/{workspace_id}/clustering/incremental")
def create_incremental(workspace_id: int, request: IncrementalRequest) -> dict[str, Any]:
    """建立增量分群工作，處理 workspace 的新專利。"""
    _validate_source_field(request.source_field)
    return _create_clustering_job(
        "clustering_incremental",
        {"workspace_id": workspace_id, "source_field": request.source_field},
        workspace_id=workspace_id,
        idempotency_key=request.idempotency_key,
    )


@router.post("/clustering/runs/{run_id}/finalize")
def create_finalize(run_id: int, request: FinalizeRequest) -> dict[str, Any]:
    """依選定 candidate 建立定案工作；run 不存在回 404。"""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        exists = conn.execute(
            "SELECT 1 FROM derived_layer.topic_runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT 1
            FROM legacy_0021.topic_candidates
            WHERE run_id = %s AND candidate_id = %s
            """,
            (run_id, request.candidate_id),
        ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if candidate is None:
        raise HTTPException(
            status_code=422,
            detail=f"candidate {request.candidate_id} does not belong to run {run_id}",
        )
    return _create_clustering_job(
        "clustering_finalize",
        {
            "run_id": run_id,
            "candidate_id": request.candidate_id,
            "selected_by": request.selected_by,
        },
        workspace_id=None,
        idempotency_key=request.idempotency_key,
    )


@router.get("/clustering/runs/{run_id}/candidates")
def get_candidates(run_id: int) -> dict[str, Any]:
    """讀取某 run 的候選主題數方案（讀分群結果，不建工作）。run 不存在回 404。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        # 0021：workspace_id/status 移到 app_layer.workflow_runs，經 workflow_run_id join 取得
        run = conn.execute(
            "SELECT tr.run_id, wr.workspace_id, tr.source_field, wr.status "
            "FROM derived_layer.topic_runs tr "
            "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
            "WHERE tr.run_id = %s",
            (run_id,),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        rows = conn.execute(
            """
            SELECT candidate_id, candidate_type, candidate_k, coherence, diversity,
                   balance, score, llm_explanation, is_selected
            FROM legacy_0021.topic_candidates
            WHERE run_id = %s ORDER BY candidate_k
            """,
            (run_id,),
        ).fetchall()
    return {
        "run_id": int(run["run_id"]),
        "workspace_id": int(run["workspace_id"]) if run["workspace_id"] is not None else None,
        "source_field": run["source_field"],
        "status": run["status"],
        "candidates": [
            {
                "candidate_id": int(r["candidate_id"]),
                "candidate_type": r["candidate_type"],
                "candidate_k": int(r["candidate_k"]),
                "coherence": float(r["coherence"]) if r["coherence"] is not None else None,
                "diversity": float(r["diversity"]) if r["diversity"] is not None else None,
                "balance": float(r["balance"]) if r["balance"] is not None else None,
                "score": float(r["score"]) if r["score"] is not None else None,
                "llm_explanation": r["llm_explanation"],
                "is_selected": bool(r["is_selected"]),
            }
            for r in rows
        ],
    }
