"""分群相關 API：建立 calibrate／finalize／incremental 工作，讀取候選方案。

backend 只建立工作與讀結果，不執行分群（那是 worker）。payload 欄名對齊
worker handlers.py 的期待。source_field 以白名單驗證，未知 workspace 由 FK
擋（轉 404）。候選查詢直接讀 topic_runs.topic_state_json，不 import 分群
引擎（避免把 BERTopic 等重模組載進 backend）。

schema 落點：0021 併表把候選併入 derived_layer.topic_runs.topic_state_json->'candidates'
（檔頭明示 topics/candidates/assignments 併入 topic_state_json）。不讀 legacy_0021.topic_candidates：
該表 run_id FK 指向凍結 archive legacy_0021.topic_runs，新 run 不在其中，寫入端無法落點，
讀寫必須同源。run 的 workspace_id/status 在 0021 移到 app_layer.workflow_runs，
需經 topic_runs.workflow_run_id join。
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
        # 一次取 run 與其候選（候選在 topic_state_json），不分兩趟查詢
        exists = conn.execute(
            "SELECT jsonb_path_exists(topic_state_json, "
            "  '$.candidates[*] ? (@.candidate_id == $cid)', jsonb_build_object('cid', %s::int)) "
            "FROM derived_layer.topic_runs WHERE run_id = %s",
            (request.candidate_id, run_id),
        ).fetchone()
        candidate = None if exists is None else (True if exists[0] else None)
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


# 候選查詢共用的 SELECT 片段：欄位與 join 兩個端點完全一致，只差 WHERE／ORDER。
_CANDIDATES_SELECT = (
    "SELECT tr.run_id, wr.workspace_id, tr.source_field, wr.status, "
    "       COALESCE(tr.topic_state_json->'candidates', '[]'::jsonb) AS candidates "
    "FROM derived_layer.topic_runs tr "
    "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
)


@router.get("/clustering/candidates")
def get_latest_candidates(workspace_id: int, source_field: str) -> dict[str, Any]:
    """解析某 workspace＋通道的**最新** run 並回其候選；沒有任何 run 回 404。

    存在理由：前端原本只能拉全域 `/tasks?limit=N` 再自行過濾出 clustering_calibrate
    的 run_id。`/tasks` 不分 workspace，且每次匯入自動產生技術＋功效兩筆 calibrate，
    與 import/finalize/report/comparison 共用同一序列——多 workspace 併用時舊
    workspace 的 calibrate 容易被擠出視窗，前端便誤報「尚未跑過分群」，但候選其實
    還在 DB 裡，使用者會被誤導去重跑一次昂貴的分群。由後端直接解析可根治。

    「最新」定義：同 workspace＋同 source_field 中 `topic_runs.run_id` 最大者，
    **不以 status 過濾**。理由有二：
    1. `run_id` 由序列產生，單調遞增，等同建立順序；topic_runs 沒有時間欄，用它排序
       比再 join 其他表取時間更直接，也不受同秒建立的並列問題影響。
    2. `workflow_runs.status` 是**整個 workflow run** 的狀態，不代表候選是否已寫入；
       候選一旦寫進 topic_state_json 就可供挑選。若以 status='succeeded' 過濾，
       finalize 之後 run 狀態改變或中途失敗的 run 都可能讓可用候選被藏起來——這正是
       本次要修掉的「候選在 DB 卻說沒有」。候選是否為空由呼叫端自行判讀（回傳
       `candidates` 可能為空陣列），status 一併回傳供前端顯示。
    """
    _validate_source_field(source_field)
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        run = conn.execute(
            _CANDIDATES_SELECT
            + "WHERE wr.workspace_id = %s AND tr.source_field = %s "
            "ORDER BY tr.run_id DESC LIMIT 1",
            (workspace_id, source_field),
        ).fetchone()
        if run is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no clustering run for workspace {workspace_id} "
                    f"source_field {source_field}"
                ),
            )
        return _candidates_response(run)


@router.get("/clustering/runs/{run_id}/candidates")
def get_candidates(run_id: int) -> dict[str, Any]:
    """讀取某 run 的候選主題數方案（讀分群結果，不建工作）。run 不存在回 404。"""
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        # 0021：workspace_id/status 移到 app_layer.workflow_runs，經 workflow_run_id join 取得；
        # 候選在 topic_state_json，與 run 同一列一次取回，不另發查詢
        run = conn.execute(
            _CANDIDATES_SELECT + "WHERE tr.run_id = %s",
            (run_id,),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return _candidates_response(run)


def _candidates_response(run: dict[str, Any]) -> dict[str, Any]:
    """把 run 列轉成候選回應（候選依 candidate_k 排序）。兩個候選端點共用同一格式。"""
    rows = sorted(run["candidates"], key=lambda r: r["candidate_k"])
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
                "coherence": float(r["coherence"]) if r.get("coherence") is not None else None,
                "diversity": float(r["diversity"]) if r.get("diversity") is not None else None,
                "balance": float(r["balance"]) if r.get("balance") is not None else None,
                "score": float(r["score"]) if r.get("score") is not None else None,
                "llm_explanation": r.get("llm_explanation"),
                "is_selected": bool(r.get("is_selected")),
            }
            for r in rows
        ],
    }
