"""Companion API 的第一輪契約測試。

這組測試只驗證 Web Companion 與 AI bridge 的 API 邊界，不連真資料庫，
避免和 Railway／Lightning 的部署狀態互相干擾。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


@dataclass(frozen=True)
class _FakeJob:
    """模擬 job_repository 回傳的 ProcessingJob 最小欄位。"""

    job_id: int
    job_type: str
    status: str
    workspace_id: int | None
    payload_json: dict[str, Any]
    result_json: dict[str, Any] | None = None
    progress_percent: int = 0
    current_stage: str = "queued"
    attempt_count: int = 0
    max_attempts: int = 1
    error_message: str | None = None


def test_companion_status_exposes_ai_bridge_boundary():
    """GET /companion/status 要說清楚 Companion 與 AI bridge 的正式邊界。"""
    resp = client.get("/api/v1/companion/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["ai_bridge"]["supported_job_types"] == ["ai:narrative"]
    assert body["ai_bridge"]["supported_cli_kinds"] == ["claude", "opencode"]
    assert body["ai_bridge"]["normal_worker_consumes_ai_jobs"] is False


def test_companion_narrative_task_creates_ai_job(monkeypatch):
    """POST /companion/narrative-tasks 要建立 ai:narrative job 並回傳輪詢位置。"""
    from backend.app.api import companion

    captured: dict[str, Any] = {}

    def fake_create_job(job_type: str, payload: dict[str, Any] | None = None, **kwargs: Any):
        """記錄 API 傳入 repository 的工作型別與 payload。"""
        captured["job_type"] = job_type
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return _FakeJob(
            job_id=123,
            job_type=job_type,
            status="queued",
            workspace_id=kwargs.get("workspace_id"),
            payload_json=payload or {},
        )

    monkeypatch.setattr(companion.jr, "create_job", fake_create_job)

    resp = client.post(
        "/api/v1/companion/narrative-tasks",
        json={
            "workspace_id": 7,
            "cli_kind": "claude",
            "based_on_version": "latest",
            "instruction": "請解釋這份報表",
            "idempotency_key": "ui-click-1",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body == {
        "run_id": 123,
        "job_type": "ai:narrative",
        "status": "queued",
        "poll_url": "/api/v1/jobs/123",
    }
    assert captured["job_type"] == "ai:narrative"
    assert captured["kwargs"] == {
        "workspace_id": 7,
        "idempotency_key": "ui-click-1",
        "max_attempts": 1,
    }
    assert captured["payload"]["cli_kind"] == "claude"
    assert captured["payload"]["based_on_version"] == "latest"
    assert captured["payload"]["instruction"] == "請解釋這份報表"


def test_companion_narrative_task_rejects_unknown_cli():
    """不支援的 CLI 類型要在 API 層阻擋，不能拖到 bridge 才失敗。"""
    resp = client.post(
        "/api/v1/companion/narrative-tasks",
        json={"workspace_id": 7, "cli_kind": "other"},
    )

    assert resp.status_code == 422


def test_companion_task_reads_ai_result_from_workflow_outputs(monkeypatch):
    """GET /companion/tasks/{run_id} 要合併 workflow_runs 狀態與 workflow_outputs 結果。"""
    from backend.app.api import companion

    job = _FakeJob(
        job_id=123,
        job_type="ai:narrative",
        status="succeeded",
        workspace_id=7,
        payload_json={"cli_kind": "claude"},
        progress_percent=100,
        current_stage="completed",
        attempt_count=1,
        max_attempts=1,
    )
    monkeypatch.setattr(companion.jr, "get_job", lambda run_id: job)
    monkeypatch.setattr(
        companion.jr,
        "fetch_job_result",
        lambda run_id, run_type: {"narratives_path": "output/full_report_latest/narratives.json"},
    )

    resp = client.get("/api/v1/companion/tasks/123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == 123
    assert body["status"] == "succeeded"
    assert body["result"] == {
        "narratives_path": "output/full_report_latest/narratives.json",
    }
    assert body["payload"] == {"cli_kind": "claude"}


def test_companion_task_rejects_non_ai_job(monkeypatch):
    """Companion 任務查詢只服務 AI 任務，避免拿一般 worker 工作混進 AI 入口。"""
    from backend.app.api import companion

    job = _FakeJob(
        job_id=55,
        job_type="report_generate",
        status="succeeded",
        workspace_id=7,
        payload_json={},
    )
    monkeypatch.setattr(companion.jr, "get_job", lambda run_id: job)

    resp = client.get("/api/v1/companion/tasks/55")

    assert resp.status_code == 404
