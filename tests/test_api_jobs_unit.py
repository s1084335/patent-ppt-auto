"""Jobs API 的不碰 DB 契約測試。"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.api import jobs as jobs_api
from backend.app.main import app


client = TestClient(app)


def _fake_job(**overrides):
    """建立 job_to_dict 可讀的最小 ProcessingJob 替身。"""
    data = {
        "job_id": 7,
        "job_type": "patent_import",
        "status": "succeeded",
        "workspace_id": None,
        "payload_json": {"blob_id": 11},
        "result_json": None,
        "progress_percent": 100,
        "current_stage": "completed",
        "attempt_count": 1,
        "max_attempts": 3,
        "error_message": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_get_job_reads_latest_workflow_output_result(monkeypatch):
    """單筆 GET /jobs/{id} 要把 workflow_outputs 的最新結果補進 result。"""
    result = {
        "inserted": 2,
        "matched_existing": 3,
        "updated": 1,
        "patent_ids": [101, 102, 103, 104, 105],
    }
    monkeypatch.setattr(jobs_api.job_repository, "get_job", lambda job_id: _fake_job(job_id=job_id))
    monkeypatch.setattr(
        jobs_api.job_repository,
        "fetch_job_result",
        lambda run_id, run_type: result,
    )

    resp = client.get("/api/v1/jobs/7")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == 7
    assert body["result"] == result


def test_get_job_keeps_row_result_when_workflow_output_missing(monkeypatch):
    """workflow_outputs 尚未讀到結果時，不可把既有 result_json 覆蓋成 None。"""
    row_result = {
        "inserted": 4,
        "matched_existing": 0,
        "updated": 1,
        "patent_ids": [201, 202, 203, 204, 205],
    }
    monkeypatch.setattr(
        jobs_api.job_repository,
        "get_job",
        lambda job_id: _fake_job(job_id=job_id, result_json=row_result),
    )
    monkeypatch.setattr(
        jobs_api.job_repository,
        "fetch_job_result",
        lambda run_id, run_type: None,
    )

    resp = client.get("/api/v1/jobs/7")

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == row_result
