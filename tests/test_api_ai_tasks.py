"""AI 任務端點（原 companion）的契約 + bearer token 認證測試。

這組測試只驗證「Web 前端建立 AI 任務」的 API 邊界與認證行為，不連真資料庫，
避免和 Railway／Lightning 的部署狀態互相干擾。

認證策略（fail closed）：PATENT_API_TOKEN 未設 → 受保護端點一律 503，
不因未設定而放行；設了則必須帶 Authorization: Bearer <token>，不符回 401。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.worker.ai_bridge import AI_JOB_TYPES


client = TestClient(app)

PREFIX = "/api/v1"
TEST_TOKEN = "test-token-abc"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture()
def token_set(monkeypatch):
    """設定 PATENT_API_TOKEN，模擬正式部署已配置 token 的情境。"""
    monkeypatch.setenv("PATENT_API_TOKEN", TEST_TOKEN)


@pytest.fixture()
def token_unset(monkeypatch):
    """清掉 PATENT_API_TOKEN，模擬未配置 token 的情境。"""
    monkeypatch.delenv("PATENT_API_TOKEN", raising=False)


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


def _patch_create_job(monkeypatch, captured: dict[str, Any]):
    """攔截 create_job，記錄呼叫參數並回傳假 job，避免測試連 DB。"""
    from backend.app.api import ai_tasks

    def fake_create_job(job_type: str, payload: dict[str, Any] | None = None, **kwargs: Any):
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

    monkeypatch.setattr(ai_tasks.jr, "create_job", fake_create_job)


# ══════════ 認證：三端點各驗 無 token／錯 token／正確 token ══════════


PROTECTED_CALLS = [
    ("get", f"{PREFIX}/ai-tasks/status", None),
    ("post", f"{PREFIX}/ai-tasks", {"cli_kind": "claude"}),
    ("get", f"{PREFIX}/ai-tasks/123", None),
]


@pytest.mark.parametrize("method,url,body", PROTECTED_CALLS)
def test_missing_token_returns_401(token_set, method, url, body):
    """已設定 token 時，未帶 Authorization 一律 401。"""
    resp = client.request(method.upper(), url, json=body)
    assert resp.status_code == 401


@pytest.mark.parametrize("method,url,body", PROTECTED_CALLS)
def test_wrong_token_returns_401(token_set, method, url, body):
    """帶錯 token 一律 401，不得洩漏端點行為差異。"""
    resp = client.request(
        method.upper(), url, json=body, headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("method,url,body", PROTECTED_CALLS)
def test_malformed_authorization_header_returns_401(token_set, method, url, body):
    """非 Bearer 格式（如裸 token）也要 401，不做寬鬆解析。"""
    resp = client.request(
        method.upper(), url, json=body, headers={"Authorization": TEST_TOKEN}
    )
    assert resp.status_code == 401


def test_correct_token_allows_status(token_set):
    """正確 token → GET /ai-tasks/status 200。"""
    resp = client.get(f"{PREFIX}/ai-tasks/status", headers=AUTH)
    assert resp.status_code == 200


def test_correct_token_allows_create(token_set, monkeypatch):
    """正確 token → POST /ai-tasks 201。"""
    _patch_create_job(monkeypatch, {})
    resp = client.post(f"{PREFIX}/ai-tasks", json={"cli_kind": "claude"}, headers=AUTH)
    assert resp.status_code == 201


def test_correct_token_allows_get_task(token_set, monkeypatch):
    """正確 token → GET /ai-tasks/{run_id} 200。"""
    from backend.app.api import ai_tasks

    job = _FakeJob(
        job_id=123,
        job_type="ai:narrative",
        status="succeeded",
        workspace_id=7,
        payload_json={},
    )
    monkeypatch.setattr(ai_tasks.jr, "get_job", lambda run_id: job)
    monkeypatch.setattr(ai_tasks.jr, "fetch_job_result", lambda run_id, run_type: None)

    resp = client.get(f"{PREFIX}/ai-tasks/123", headers=AUTH)
    assert resp.status_code == 200


# ══════════ 未設 token 的策略：fail closed ══════════


@pytest.mark.parametrize("method,url,body", PROTECTED_CALLS)
def test_unset_token_rejects_all_requests(token_unset, method, url, body):
    """未設 PATENT_API_TOKEN → 503 拒絕，且訊息說明要設哪個環境變數。"""
    resp = client.request(method.upper(), url, json=body)
    assert resp.status_code == 503
    assert "PATENT_API_TOKEN" in resp.text


def test_unset_token_rejects_even_with_bearer_header(token_unset):
    """未設 token 時，就算自己帶 Bearer 也不能通過（不可被猜中而放行）。"""
    resp = client.get(f"{PREFIX}/ai-tasks/status", headers=AUTH)
    assert resp.status_code == 503


def test_blank_token_treated_as_unset(monkeypatch):
    """空字串／空白 token 視同未設定，避免部署誤填空值就變裸奔。"""
    monkeypatch.setenv("PATENT_API_TOKEN", "   ")
    resp = client.get(f"{PREFIX}/ai-tasks/status", headers=AUTH)
    assert resp.status_code == 503


# ══════════ 改名後的路由與既有契約 ══════════


def test_status_exposes_ai_bridge_boundary(token_set):
    """GET /ai-tasks/status 要說清楚前端入口與 AI bridge 的正式邊界。"""
    resp = client.get(f"{PREFIX}/ai-tasks/status", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    # AI bridge 支援的任務類型會隨新 AI 功能增加（目前：報表解讀 ai:narrative、
    # 主題標籤 ai:topic_label）；斷言「涵蓋且與 bridge 常數一致」而非固定字面值。
    assert set(body["ai_bridge"]["supported_job_types"]) == set(AI_JOB_TYPES)
    assert "ai:narrative" in body["ai_bridge"]["supported_job_types"]
    assert body["ai_bridge"]["supported_cli_kinds"] == ["claude", "opencode"]
    assert body["ai_bridge"]["normal_worker_consumes_ai_jobs"] is False


def test_create_task_creates_ai_job(token_set, monkeypatch):
    """POST /ai-tasks 要建立 ai:narrative job 並回傳輪詢位置。"""
    captured: dict[str, Any] = {}
    _patch_create_job(monkeypatch, captured)

    resp = client.post(
        f"{PREFIX}/ai-tasks",
        json={
            "workspace_id": 7,
            "cli_kind": "claude",
            "based_on_version": "latest",
            "instruction": "請解釋這份報表",
            "idempotency_key": "ui-click-1",
        },
        headers=AUTH,
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


def test_create_task_rejects_unknown_cli(token_set):
    """不支援的 CLI 類型要在 API 層阻擋，不能拖到 bridge 才失敗。"""
    resp = client.post(
        f"{PREFIX}/ai-tasks",
        json={"workspace_id": 7, "cli_kind": "other"},
        headers=AUTH,
    )

    assert resp.status_code == 422


def test_create_task_accepts_task_type_shape(token_set, monkeypatch):
    """整併自 events.py 的舊格式 {task_type, params} 仍可建任務（不製造第二個端點）。"""
    captured: dict[str, Any] = {}
    _patch_create_job(monkeypatch, captured)

    resp = client.post(
        f"{PREFIX}/ai-tasks",
        json={"task_type": "ai:narrative", "params": {"patent_ids": [1]}},
        headers=AUTH,
    )

    assert resp.status_code == 201
    assert captured["job_type"] == "ai:narrative"
    assert captured["payload"]["patent_ids"] == [1]


def test_create_task_rejects_unsupported_task_type(token_set):
    """舊格式帶不支援的 task_type → 422。"""
    resp = client.post(
        f"{PREFIX}/ai-tasks",
        json={"task_type": "ai:invalid", "params": {}},
        headers=AUTH,
    )

    assert resp.status_code == 422


def test_get_task_reads_ai_result_from_workflow_outputs(token_set, monkeypatch):
    """GET /ai-tasks/{run_id} 要合併 workflow_runs 狀態與 workflow_outputs 結果。"""
    from backend.app.api import ai_tasks

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
    monkeypatch.setattr(ai_tasks.jr, "get_job", lambda run_id: job)
    monkeypatch.setattr(
        ai_tasks.jr,
        "fetch_job_result",
        lambda run_id, run_type: {"narratives_path": "output/full_report_latest/narratives.json"},
    )

    resp = client.get(f"{PREFIX}/ai-tasks/123", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == 123
    assert body["status"] == "succeeded"
    assert body["result"] == {
        "narratives_path": "output/full_report_latest/narratives.json",
    }
    assert body["payload"] == {"cli_kind": "claude"}


def test_get_task_rejects_non_ai_job(token_set, monkeypatch):
    """AI 任務查詢只服務 AI 任務，避免拿一般 worker 工作混進 AI 入口。"""
    from backend.app.api import ai_tasks

    job = _FakeJob(
        job_id=55,
        job_type="report_generate",
        status="succeeded",
        workspace_id=7,
        payload_json={},
    )
    monkeypatch.setattr(ai_tasks.jr, "get_job", lambda run_id: job)

    resp = client.get(f"{PREFIX}/ai-tasks/55", headers=AUTH)

    assert resp.status_code == 404


def test_old_companion_narrative_route_is_gone(token_set):
    """舊 POST /companion/narrative-tasks 入口不得殘留（與 /ai-tasks 語意重疊）。

    ⚠ 2026-07-24 定案：GET /companion/status 予以保留（launcher 健檢需要），故不再斷言其消失；
    僅斷言重疊的 narrative-tasks 入口已移除。原 test_old_companion_routes_are_gone 對
    /companion/status 回 404 的斷言已隨此定案刪除。"""
    assert (
        client.post(
            f"{PREFIX}/companion/narrative-tasks", json={"cli_kind": "claude"}, headers=AUTH
        ).status_code
        == 404
    )


def test_public_endpoints_still_open():
    """本輪不動的既有端點（如 /health）不受認證影響，前端才不會整頁壞掉。"""
    assert client.get(f"{PREFIX}/health").status_code == 200
