"""桌面捷徑 Launcher（PowerShell）與 Companion 狀態端點的測試。

分兩部分：

1. `scripts/patent_launcher.ps1` 的決策邏輯。PowerShell 腳本難以單元測試，
   因此腳本內建 `-DryRun` 模式：只做偵測、把「決定要做什麼」以單行 JSON
   （`LAUNCH_PLAN <json>`）印到 stdout，不真的啟動 Companion／backend／瀏覽器。
   本測試以暫存目錄放偽造 heartbeat，**實跑**腳本並斷言計畫內容，
   不靠肉眼檢查；也因此不會真的呼叫 Claude CLI、不註冊排程、不建捷徑。

2. `GET /api/v1/companion/status` 的回應形狀（不連 DB，只驗契約）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts" / "patent_launcher.ps1"
SHORTCUT_SCRIPT = PROJECT_ROOT / "scripts" / "patent_shortcut_install.ps1"
STARTUP_SCRIPT = PROJECT_ROOT / "scripts" / "companion_startup_install.ps1"

client = TestClient(app)
PREFIX = "/api/v1"

# Windows PowerShell 5.1 的 stdout/stderr 走系統 ANSI（繁中機器為 cp950），不是 UTF-8。
# 以 utf-8 解會在腳本輸出含中文時丟 UnicodeDecodeError，讓整批 launcher 測試假性失敗
# （2026-07-27 實測：11 個 launcher 測試皆因此而非因邏輯錯誤而紅）。
PS_OUTPUT_ENCODING = "mbcs" if os.name == "nt" else "utf-8"


# ── PowerShell dry-run 輔助 ────────────────────────────────────

powershell = shutil.which("powershell") or shutil.which("pwsh")
requires_powershell = pytest.mark.skipif(
    powershell is None, reason="本機沒有 powershell/pwsh，無法實跑 launcher 腳本"
)


def _write_heartbeat(state_dir: Path, *, status: str, age_seconds: float) -> Path:
    """寫一份指定新舊程度與狀態的 heartbeat，模擬 Companion 各種存活情形。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "ai_bridge_heartbeat.json"
    updated_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    path.write_text(
        json.dumps(
            {
                "updated_at": updated_at.isoformat(),
                "status": status,
                "worker_id": "test-bridge",
                "pid": 4242,
                "stats": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def run_launcher(state_dir: Path, **kwargs: str) -> dict:
    """以 -DryRun 實跑 launcher 並解析它印出的 LAUNCH_PLAN JSON。"""
    args = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(LAUNCHER),
        "-StateDir",
        str(state_dir),
    ]
    for key, value in kwargs.items():
        args.extend([f"-{key}", str(value)])
    # -DryRun 是 switch，放最後避免被誤解析為下一個具名參數的值。
    args.append("-DryRun")
    env = dict(os.environ)
    # 清掉可能殘留的環境覆蓋，確保測到的是參數與預設值行為。
    for name in ("PATENT_FRONTEND_URL", "AI_BRIDGE_STATE_DIR", "PATENT_START_BACKEND"):
        env.pop(name, None)
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding=PS_OUTPUT_ENCODING, env=env, timeout=120
    )
    assert proc.returncode == 0, f"launcher 失敗：\n{proc.stdout}\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("LAUNCH_PLAN ")]
    assert lines, f"stdout 沒有 LAUNCH_PLAN：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(lines[-1][len("LAUNCH_PLAN ") :])


# ── A. Launcher 冪等與可設定性 ─────────────────────────────────


@requires_powershell
def test_launcher_script_exists():
    """launcher 與捷徑腳本必須存在於 scripts/。"""
    assert LAUNCHER.is_file()
    assert SHORTCUT_SCRIPT.is_file()


@requires_powershell
def test_heartbeat_ok_skips_companion_start(tmp_path: Path):
    """heartbeat 新鮮且 status 非 stopped → 判定已在跑，不得重複啟動 Companion。"""
    _write_heartbeat(tmp_path, status="running", age_seconds=5)
    plan = run_launcher(tmp_path)
    assert plan["companion_state"] == "ok"
    assert plan["start_companion"] is False


@requires_powershell
def test_heartbeat_stopped_starts_companion(tmp_path: Path):
    """heartbeat 標記 stopped（正常關閉）→ 必須重新啟動 Companion。"""
    _write_heartbeat(tmp_path, status="stopped", age_seconds=5)
    plan = run_launcher(tmp_path)
    assert plan["companion_state"] == "stopped"
    assert plan["start_companion"] is True


@requires_powershell
def test_heartbeat_stale_starts_companion(tmp_path: Path):
    """heartbeat 過舊（多半已崩潰）→ 必須重新啟動 Companion。"""
    _write_heartbeat(tmp_path, status="running", age_seconds=100000)
    plan = run_launcher(tmp_path)
    assert plan["companion_state"] == "stale"
    assert plan["start_companion"] is True


@requires_powershell
def test_heartbeat_missing_starts_companion(tmp_path: Path):
    """從未跑過（沒有 heartbeat 檔）→ 必須啟動 Companion。"""
    plan = run_launcher(tmp_path)
    assert plan["companion_state"] == "missing"
    assert plan["start_companion"] is True


@requires_powershell
def test_frontend_url_and_state_dir_are_overridable(tmp_path: Path):
    """網址與 StateDir 不得寫死：參數可覆蓋，且預設為 127.0.0.1:8000。"""
    default_plan = run_launcher(tmp_path)
    assert default_plan["frontend_url"] == "http://127.0.0.1:8000"
    assert Path(default_plan["state_dir"]) == tmp_path

    custom = run_launcher(tmp_path, FrontendUrl="https://patent.example.com")
    assert custom["frontend_url"] == "https://patent.example.com"


@requires_powershell
def test_frontend_url_from_environment(tmp_path: Path):
    """未給參數時，環境變數 PATENT_FRONTEND_URL 可覆蓋前端網址（部署可攜性）。"""
    args = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(LAUNCHER),
        "-StateDir",
        str(tmp_path),
        "-DryRun",
    ]
    env = dict(os.environ)
    env["PATENT_FRONTEND_URL"] = "https://patent.up.railway.app"
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding=PS_OUTPUT_ENCODING, env=env, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("LAUNCH_PLAN ")][-1]
    plan = json.loads(line[len("LAUNCH_PLAN ") :])
    assert plan["frontend_url"] == "https://patent.up.railway.app"


@requires_powershell
def test_remote_frontend_never_starts_local_backend(tmp_path: Path):
    """前端指向遠端網址時，即使連不上也不得嘗試起本機 backend。"""
    plan = run_launcher(tmp_path, FrontendUrl="https://patent.example.invalid")
    assert plan["frontend_reachable"] is False
    assert plan["start_backend"] is False
    assert plan["backend_skip_reason"] == "remote-url"


@requires_powershell
def test_start_backend_switch_can_be_disabled_for_local(tmp_path: Path):
    """本機網址時是否順便起 backend 必須可設定（-StartBackend Never 關閉）。"""
    plan = run_launcher(tmp_path, StartBackend="Never")
    assert plan["start_backend"] is False


@requires_powershell
def test_plan_always_opens_browser(tmp_path: Path):
    """不論 Companion／backend 狀態如何，最終一定會開瀏覽器到前端網址。"""
    plan = run_launcher(tmp_path)
    assert plan["open_browser"] is True
    assert plan["browser_url"] == plan["frontend_url"]


@requires_powershell
def test_dry_run_does_not_touch_system(tmp_path: Path):
    """-DryRun 不得產生啟動包裝、不得寫 heartbeat，確保測試不動使用者機器。"""
    run_launcher(tmp_path)
    assert not (tmp_path / "companion_serve.cmd").exists()
    assert not (tmp_path / "ai_bridge_heartbeat.json").exists()


# ── B. 桌面捷徑建立／移除 ──────────────────────────────────────


@requires_powershell
def test_shortcut_create_and_remove_in_temp_dir(tmp_path: Path):
    """捷徑腳本在**暫存目錄**建立 .lnk 並可移除；不碰使用者真實桌面。

    同時驗證捷徑帶 -WindowStyle Hidden，以及 .lnk 的 WindowStyle=7（最小化）。
    """
    name = "PatentLauncherTest"

    def run(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(SHORTCUT_SCRIPT),
                "-ShortcutDir", str(tmp_path),
                "-ShortcutName", name,
                *extra,
            ],
            capture_output=True, text=True, encoding=PS_OUTPUT_ENCODING, timeout=120,
        )

    created = run("-FrontendUrl", "https://patent.example.com")
    assert created.returncode == 0, created.stderr
    lnk = tmp_path / f"{name}.lnk"
    assert lnk.is_file()
    assert "-WindowStyle Hidden" in created.stdout
    assert "https://patent.example.com" in created.stdout

    # 讀回 .lnk 確認視窗樣式真的寫進捷徑（不是只印在訊息裡）。
    probe = subprocess.run(
        [
            powershell, "-NoProfile", "-Command",
            "$s=New-Object -ComObject WScript.Shell;"
            f"$l=$s.CreateShortcut('{lnk}');"
            "Write-Output $l.WindowStyle; Write-Output $l.Arguments",
        ],
        capture_output=True, text=True, encoding=PS_OUTPUT_ENCODING, timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    lines = [ln.strip() for ln in probe.stdout.splitlines() if ln.strip()]
    assert lines[0] == "7"
    assert "-WindowStyle Hidden" in lines[1]

    removed = run("-Remove")
    assert removed.returncode == 0, removed.stderr
    assert not lnk.exists()

    # 冪等：再移除一次不得報錯。
    again = run("-Remove")
    assert again.returncode == 0, again.stderr


@requires_powershell
def test_companion_startup_wrapper_uses_cmd_and_bootstrap_log(tmp_path: Path):
    """startup installer 產生的隱藏啟動器要能在中文路徑下啟動，且保留啟動錯誤 log。"""
    proc = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& '{STARTUP_SCRIPT}' -StateDir '{tmp_path}' -StartupDir '{tmp_path}' -StartNow:$false",
        ],
        capture_output=True,
        text=True,
        encoding=PS_OUTPUT_ENCODING,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    cmd = (tmp_path / "companion_serve.cmd").read_text(encoding="mbcs")
    assert "companion_bootstrap.log" in cmd

    # ⚠ .vbs 必須以系統 ANSI(mbcs/cp950) 存檔，不得帶 UTF-8 BOM。
    # 實測 2026-07-27：wscript.exe 讀 .vbs 一律用系統 ANSI，不認 UTF-8 BOM。
    # 存成 UTF-8 with BOM 時，cp950 解讀會把 BOM 變成「嚜?」並吃掉該行換行，
    # 使下一行的 `Dim sh` 被併進註解（變數未宣告），中文專案路徑也整段變亂碼
    # （D:\力山\... → D:\?控\...）→ wscript 靜默失敗、Companion 完全不啟動。
    vbs_bytes = (tmp_path / "companion_serve_hidden.vbs").read_bytes()
    assert not vbs_bytes.startswith(b"\xef\xbb\xbf"), (
        ".vbs 不得含 UTF-8 BOM——wscript 以 ANSI 讀取，BOM 會讓首行解析失敗"
    )
    vbs = vbs_bytes.decode("mbcs")  # 用 wscript 的實際讀法解碼
    assert "cmd.exe /c" in vbs
    assert "companion_serve.cmd" in vbs
    # 中文專案路徑經 ANSI 往返後必須仍然存在（BOM/UTF-8 存檔會在此變亂碼）
    assert str(PROJECT_ROOT) in vbs, "VBS 內的專案路徑在 ANSI 解碼後毀損"
    # 每行獨立：Dim 宣告不得被前一行註解吃掉
    assert any(line.strip() == "Dim sh" for line in vbs.splitlines()), (
        "Dim sh 應自成一行；被併入註解代表換行在編碼轉換中遺失"
    )


# ── C. Companion 狀態端點 ─────────────────────────────────────


@pytest.fixture()
def token_set(monkeypatch):
    """設定 PATENT_API_TOKEN，模擬正式部署已配置 token 的情境。"""
    monkeypatch.setenv("PATENT_API_TOKEN", "test-token-abc")
    return {"Authorization": "Bearer test-token-abc"}


def test_companion_status_shape_when_missing(tmp_path: Path, monkeypatch, token_set):
    """heartbeat 不存在 → state=missing，且明確標示本機可讀性（跨容器限制）。"""
    monkeypatch.setenv("AI_BRIDGE_STATE_DIR", str(tmp_path))
    resp = client.get(f"{PREFIX}/companion/status", headers=token_set)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "missing"
    assert body["alive"] is False
    assert body["last_heartbeat_at"] is None
    # 區分「Companion 沒跑」vs「此 backend 讀不到本機 heartbeat」
    assert "heartbeat_readable" in body
    assert "note" in body


def test_companion_status_shape_when_ok(tmp_path: Path, monkeypatch, token_set):
    """heartbeat 新鮮 → state=ok，並回報最後心跳時間與 age。"""
    monkeypatch.setenv("AI_BRIDGE_STATE_DIR", str(tmp_path))
    _write_heartbeat(tmp_path, status="running", age_seconds=3)
    resp = client.get(f"{PREFIX}/companion/status", headers=token_set)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "ok"
    assert body["alive"] is True
    assert body["heartbeat_readable"] is True
    assert body["last_heartbeat_at"] is not None
    assert body["age_seconds"] is not None
    assert body["worker_id"] == "test-bridge"


def test_companion_status_stale_and_stopped(tmp_path: Path, monkeypatch, token_set):
    """stale 與 stopped 兩種狀態都要能被端點正確區分。"""
    monkeypatch.setenv("AI_BRIDGE_STATE_DIR", str(tmp_path))

    _write_heartbeat(tmp_path, status="running", age_seconds=100000)
    stale = client.get(f"{PREFIX}/companion/status", headers=token_set).json()
    assert stale["state"] == "stale"
    assert stale["alive"] is False

    _write_heartbeat(tmp_path, status="stopped", age_seconds=3)
    stopped = client.get(f"{PREFIX}/companion/status", headers=token_set).json()
    assert stopped["state"] == "stopped"
    assert stopped["alive"] is False


def test_companion_status_requires_token(tmp_path: Path, monkeypatch):
    """未帶 token → 401（沿用既有 bearer 認證，不另開無認證端點）。"""
    monkeypatch.setenv("PATENT_API_TOKEN", "test-token-abc")
    monkeypatch.setenv("AI_BRIDGE_STATE_DIR", str(tmp_path))
    assert client.get(f"{PREFIX}/companion/status").status_code == 401
