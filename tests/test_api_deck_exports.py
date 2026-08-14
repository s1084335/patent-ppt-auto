"""「匯出報告」頁的 deck 交付物 API（add-deck-delivery-line tasks 3.3 後端面）。

## 端點契約（design §6）

- `GET /deck-exports`：deck 紀錄清單（時間序新到舊）——workflow_runs 的
  `ai:report_deck` job ＋ workflow_outputs 的 manifest 合併。
- `GET /deck-exports/{run_id}/pages/{name}`：逐頁 PNG（「先看到、再下載」的
  「看」——backend 自 artifact root 供圖）。
- `GET /deck-exports/{run_id}/pptx`：下載 pptx（使用者主動按；串流前先驗
  SHA-256 與 manifest 相符，檔案被動過要 fail loud 不供檔）。

## 安全邊界

供檔路徑一律由 manifest 的相對 key 解析，並驗證解析結果仍在 artifact root
之內（沿 `artifacts.py` 的 escape 檢查前例）——`{name}` 是使用者輸入。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)
PREFIX = "/api/v1"


@dataclass(frozen=True)
class _FakeJob:
    job_id: int
    job_type: str = "ai:report_deck"
    status: str = "succeeded"
    workspace_id: int | None = None
    payload_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] | None = None
    progress_percent: int = 100
    current_stage: str = "完成"
    attempt_count: int = 1
    max_attempts: int = 1
    error_message: str | None = None


def _manifest(version: str, sha: str) -> dict[str, Any]:
    return {
        "based_on_version": version,
        "pptx_key": f"{version}/deck.pptx",
        "sha256": sha,
        "size_bytes": 4,
        "page_count": 2,
        "page_keys": [f"{version}/pages/page01.png", f"{version}/pages/page02.png"],
        "visual_rounds": 1,
        "visual_log": [{"round": 1, "source": "cli_review", "findings": []}],
        "cli_kind": "claude",
    }


@pytest.fixture()
def deck_env(monkeypatch, tmp_path):
    """假 artifact root（含一版產物）＋假 job 資料層。"""
    version = "report_trial_20990101_000000"
    vdir = tmp_path / version
    (vdir / "pages").mkdir(parents=True)
    pptx = vdir / "deck.pptx"
    pptx.write_bytes(b"PPTX")
    (vdir / "pages" / "page01.png").write_bytes(b"PNG1")
    (vdir / "pages" / "page02.png").write_bytes(b"PNG2")
    sha = hashlib.sha256(b"PPTX").hexdigest()
    manifest = _manifest(version, sha)
    (vdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("DECK_ARTIFACT_ROOT", str(tmp_path))

    from backend.app.api import deck_exports as mod

    job = _FakeJob(job_id=77, payload_json={"based_on_version": version})
    monkeypatch.setattr(mod.jr, "list_jobs",
                        lambda **kw: [job] if kw.get("job_type") == "ai:report_deck" else [])
    monkeypatch.setattr(mod.jr, "get_job", lambda jid: job if jid == 77 else None)
    monkeypatch.setattr(mod.jr, "fetch_job_result",
                        lambda jid, jt: manifest if jid == 77 else None)
    return {"version": version, "sha": sha, "pptx": pptx, "manifest": manifest}


class TestList:
    def test_list_merges_job_and_manifest(self, deck_env):
        resp = client.get(f"{PREFIX}/deck-exports")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["run_id"] == 77
        assert item["status"] == "succeeded"
        assert item["based_on_version"] == deck_env["version"]
        assert item["page_count"] == 2
        assert item["visual_rounds"] == 1
        # 逐頁預覽用的 URL 由後端給，前端不拼 key（key 形制變了前端不用改）
        assert item["page_urls"][0].endswith("/deck-exports/77/pages/page01.png")
        assert item["pptx_url"].endswith("/deck-exports/77/pptx")


class TestPages:
    def test_serves_page_png(self, deck_env):
        resp = client.get(f"{PREFIX}/deck-exports/77/pages/page01.png")
        assert resp.status_code == 200
        assert resp.content == b"PNG1"
        assert resp.headers["content-type"].startswith("image/png")

    def test_unknown_page_404(self, deck_env):
        assert client.get(f"{PREFIX}/deck-exports/77/pages/nope.png").status_code == 404

    def test_traversal_rejected(self, deck_env):
        """`{name}` 是使用者輸入——不在 manifest page_keys 內的一律 404。"""
        resp = client.get(f"{PREFIX}/deck-exports/77/pages/..%2F..%2Fmanifest.json")
        assert resp.status_code == 404

    def test_job_without_result_404(self, deck_env, monkeypatch):
        from backend.app.api import deck_exports as mod
        monkeypatch.setattr(mod.jr, "fetch_job_result", lambda jid, jt: None)
        assert client.get(f"{PREFIX}/deck-exports/77/pages/page01.png").status_code == 404


class TestPptx:
    def test_download_streams_with_filename(self, deck_env):
        resp = client.get(f"{PREFIX}/deck-exports/77/pptx")
        assert resp.status_code == 200
        assert resp.content == b"PPTX"
        assert "deck" in resp.headers.get("content-disposition", "")

    def test_hash_mismatch_fails_loud(self, deck_env):
        """檔案與 manifest SHA-256 不符＝產物被動過——不供檔，回 409。

        ⚠ 靜默供出改過的檔比 404 危險：使用者拿到的簡報和目視驗過的不是同一份。
        """
        deck_env["pptx"].write_bytes(b"TAMPERED")
        resp = client.get(f"{PREFIX}/deck-exports/77/pptx")
        assert resp.status_code == 409
        assert "SHA-256" in resp.json()["detail"]
