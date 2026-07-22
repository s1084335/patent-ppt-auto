"""Case comparison API for creating and tracking case_comparison jobs.

POST /api/v1/comparisons creates a job with idempotency support.
GET /api/v1/comparisons/{job_id} returns job status, result, and element_analysis.
POST /api/v1/comparisons/{job_id}/target saves the comparison target.
POST /api/v1/comparisons/{job_id}/understanding saves the AI understanding draft.
POST /api/v1/comparisons/{job_id}/understanding/approve approves an understanding version.
POST /api/v1/comparisons/{job_id}/element-analysis saves element-level comparison results.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.app.comparison.comparison_store import ComparisonStore, GateNotApprovedError
from backend.app.comparison.verdict import VerdictError
from backend.app.db import job_repository as jr
from backend.app.db.connection import get_connection_kwargs


VALID_COMPARISON_TYPES: frozenset[str] = frozenset({"claim_or_technical"})

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


def _request_fingerprint(*, job_type: str, payload: dict[str, Any],
                         workspace_id: int | None, max_attempts: int) -> str:
    """Build a stable hash for an idempotent job request."""
    canonical = {"job_type": job_type, "payload": payload,
                 "workspace_id": workspace_id, "max_attempts": max_attempts}
    data = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _effective_request_key(*, idempotency_key: str | None, job_type: str,
                           payload: dict[str, Any], workspace_id: int | None,
                           max_attempts: int) -> str | None:
    """Combine the caller key and request fingerprint so changed payloads create new jobs."""
    if idempotency_key is None:
        return None
    fingerprint = _request_fingerprint(
        job_type=job_type, payload=payload,
        workspace_id=workspace_id, max_attempts=max_attempts)
    return f"{idempotency_key}:{fingerprint}"


def _find_existing_comparison(request_key: str) -> jr.ProcessingJob | None:
    """Return the existing case_comparison job for a request key, or None."""
    with psycopg.connect(**get_connection_kwargs()) as conn:
        row = conn.execute(
            "SELECT run_id FROM app_layer.workflow_runs "
            "WHERE request_key = %s AND run_type = 'case_comparison'",
            (request_key,)).fetchone()
    if not row:
        return None
    return jr.get_job(row[0])


def _job_to_response(job: jr.ProcessingJob, workspace_id: Any) -> dict[str, Any]:
    """Convert a workflow job record into the API response shape."""
    base = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "workspace_id": workspace_id,
        "current_stage": job.current_stage,
        "progress_percent": job.progress_percent,
    }
    return base


@router.post("", status_code=202)
def create_comparison(body: dict[str, Any]):
    """Create a case_comparison job after validating the required request fields."""
    workspace_id = body.get("workspace_id")
    case_title = body.get("case_title")
    case_text = body.get("case_text")
    comparison_type = body.get("comparison_type")
    idempotency_key = body.get("idempotency_key")

    if not workspace_id or not isinstance(workspace_id, int):
        raise HTTPException(status_code=422, detail="workspace_id is required (int)")
    if not case_title or not isinstance(case_title, str):
        raise HTTPException(status_code=422, detail="case_title is required")
    if not case_text or not isinstance(case_text, str):
        raise HTTPException(status_code=422, detail="case_text is required")
    if not comparison_type or not isinstance(comparison_type, str):
        raise HTTPException(status_code=422, detail="comparison_type is required")
    if comparison_type not in VALID_COMPARISON_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported comparison_type: {comparison_type}",
        )

    payload = {
        "workspace_id": workspace_id,
        "case_title": case_title,
        "case_text": case_text,
        "comparison_type": comparison_type,
        "_verify_marker_comparison": True,
    }

    if idempotency_key:
        request_key = _effective_request_key(
            idempotency_key=idempotency_key,
            job_type="case_comparison",
            payload=payload,
            workspace_id=None,
            max_attempts=3,
        )
        existing = _find_existing_comparison(request_key)
        if existing:
            return JSONResponse(
                status_code=200,
                content=_job_to_response(existing, workspace_id),
            )

    job = jr.create_job(
        "case_comparison",
        payload=payload,
        workspace_id=None,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        status_code=202,
        content=_job_to_response(job, workspace_id),
    )


@router.get("/{job_id}")
def get_comparison(job_id: int):
    """Return a case_comparison job with its stored result and latest element analysis."""
    job = jr.get_job(job_id)
    if job is None or job.job_type != "case_comparison":
        raise HTTPException(status_code=404, detail="case_comparison job not found")
    ws_id = (job.payload_json or {}).get("workspace_id")
    base = _job_to_response(job, ws_id)
    base["result"] = jr.fetch_job_result(job_id, "case_comparison")
    store = ComparisonStore()
    ea = store.get_latest_element_analysis(job_id)
    if ea is not None:
        base["element_analysis_version"] = ea["version"]
        base["element_analysis"] = ea["data"]
    else:
        base["element_analysis_version"] = None
        base["element_analysis"] = None
    return base


def _require_comparison_job(job_id: int) -> jr.ProcessingJob:
    """Require an existing case_comparison job; raise 404 otherwise."""
    job = jr.get_job(job_id)
    if job is None or job.job_type != "case_comparison":
        raise HTTPException(status_code=404, detail="case_comparison job not found")
    return job


@router.post("/{job_id}/target")
def save_target(job_id: int, body: dict[str, Any]):
    """Save the comparison target payload."""
    _require_comparison_job(job_id)
    store = ComparisonStore()
    try:
        version = store.save_target(job_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"job_id": job_id, "output_type": "target", "version": version}


@router.post("/{job_id}/understanding")
def save_understanding(job_id: int, body: dict[str, Any]):
    """Save the AI understanding draft for the comparison case."""
    _require_comparison_job(job_id)
    store = ComparisonStore()
    version = store.save_understanding(job_id, body)
    return {"job_id": job_id, "output_type": "understanding", "version": version}


@router.post("/{job_id}/understanding/approve")
def approve_understanding(job_id: int, body: dict[str, Any]):
    """Approve an understanding version before downstream analysis."""
    _require_comparison_job(job_id)
    understanding_version = body.get("understanding_version")
    approved_by = body.get("approved_by")
    revised_understanding = body.get("revised_understanding")
    if not isinstance(understanding_version, int):
        raise HTTPException(status_code=422, detail="understanding_version is required (int)")
    if not approved_by or not isinstance(approved_by, str) or not approved_by.strip():
        raise HTTPException(status_code=422, detail="approved_by is required")
    store = ComparisonStore()
    try:
        version = store.approve_understanding(
            job_id,
            understanding_version=understanding_version,
            approved_by=approved_by,
            revised_understanding=revised_understanding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except GateNotApprovedError:
        raise HTTPException(
            status_code=409,
            detail=f"understanding version v{understanding_version} does not exist",
        )
    return {"job_id": job_id, "output_type": "understanding_approval", "version": version}


@router.post("/{job_id}/element-analysis")
def save_element_analysis(job_id: int, body: dict[str, Any]):
    """Save element-level comparison results after understanding approval."""
    _require_comparison_job(job_id)
    store = ComparisonStore()
    try:
        version = store.save_element_analysis(job_id, body)
    except VerdictError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except GateNotApprovedError:
        raise HTTPException(
            status_code=409,
            detail="understanding is not approved; cannot save element_analysis",
        )
    return {"job_id": job_id, "output_type": "element_analysis", "version": version}