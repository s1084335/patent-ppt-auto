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


def ensure_patent_sberta(*, skip: bool | None = None) -> None:
    """worker 啟動前確保 PatentSBERTa 權重就位；缺就從 HuggingFace 下載一次。

    背景（2026-07-23 定案：方案 B）：正式 image 刻意不包 837MB 權重，而 embeddings
    推論走 `local_files_only=True`（權重 SHA-256 即模型版本，確保 embedding 可重現）。
    兩者相加的結果是──雲端 worker 一跑 embeddings 就 FileNotFoundError。故在此補上
    「啟動時下載到本機路徑」這一段，推論本身仍維持 local_files_only，可重現性不變。

    只在 worker 執行：backend 不做 embedding，不必為 837MB 下載拖長冷啟動。
    已存在即直接跳過，故掛 volume 後只有第一次會下載。
    `SKIP_MODEL_DOWNLOAD=1` 可略過（本機開發或權重已用其他方式掛載時）。
    """
    if skip is None:
        skip = os.getenv("SKIP_MODEL_DOWNLOAD", "").strip().lower() in {"1", "true", "yes"}
    if skip:
        print("[deploy] SKIP_MODEL_DOWNLOAD 已設，略過 PatentSBERTa 權重檢查", flush=True)
        return

    # 延遲載入：backend 角色不該為了這段付出 import 成本。
    from backend.app.clustering.db_writer import default_patent_sberta_model_path
    from backend.app.clustering.model import PATENT_SBERTA_MODEL, PATENT_SBERTA_REVISION

    target = default_patent_sberta_model_path()
    if target.is_dir() and any(target.iterdir()):
        print(f"[deploy] PatentSBERTa 權重已存在：{target}", flush=True)
        return

    print(
        f"[deploy] 下載 PatentSBERTa（{PATENT_SBERTA_MODEL}@{PATENT_SBERTA_REVISION[:12]}）"
        f"到 {target} …",
        flush=True,
    )
    from huggingface_hub import snapshot_download

    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=PATENT_SBERTA_MODEL,
        # 釘 commit：上游改版不會讓雲端與本機拿到不同權重（向量將無法比較）。
        revision=PATENT_SBERTA_REVISION,
        local_dir=str(target),
        # 權重是長期資產，直接落在 local_dir，不另外在 HF cache 佔一份。
        local_dir_use_symlinks=False,
    )
    print(f"[deploy] PatentSBERTa 權重就位：{target}", flush=True)


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
        # 權重就位是 worker 的前置條件：缺權重時 embeddings job 會 FileNotFoundError，
        # 與其讓每個 job 各自失敗，不如啟動時一次補齊。
        ensure_patent_sberta()
        _run_process(_worker_command())
        return
    raise SystemExit(f"unsupported APP_ROLE: {role!r}; expected 'backend' or 'worker'")


if __name__ == "__main__":
    main()
