"""正式部署入口。

同一個 Docker image 同時服務 backend 與 worker；部署平台只需要設定 APP_ROLE。
backend 會使用平台注入的 PORT，worker 則走既有 worker shim，不依賴對外 port。
"""

from __future__ import annotations

import os
import subprocess
import sys


def _backend_command() -> list[str]:
    """組出 backend web server 啟動指令，避免 Dockerfile 寫死 Railway port。"""
    port = os.getenv("PORT", "8000").strip() or "8000"
    if not port.isdecimal():
        raise SystemExit(f"PORT must be an integer, got {port!r}")
    return [
        "python",
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        port,
    ]


def _worker_command() -> list[str]:
    """組出 worker 啟動指令；實際 serve 預設由 backend.app.worker.main 補上。"""
    return ["python", "-m", "backend.app.worker.main"]


def _run_process(command: list[str]) -> None:
    """用目前程序交給子程序執行，保留容器日誌與退出碼語意。"""
    completed = subprocess.run(command, check=False)
    raise SystemExit(completed.returncode)


def main() -> None:
    """依 APP_ROLE 選擇 backend 或 worker，讓 Railway 兩個服務可共用 image。"""
    role = os.getenv("APP_ROLE", "backend").strip().lower() or "backend"
    if role == "backend":
        _run_process(_backend_command())
        return
    if role == "worker":
        _run_process(_worker_command())
        return
    raise SystemExit(f"unsupported APP_ROLE: {role!r}; expected 'backend' or 'worker'")


if __name__ == "__main__":
    main()
