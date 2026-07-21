"""後端共用設定。

此檔在 FastAPI 與 worker 匯入時都會載入，因此環境變數解析必須保守：
格式錯誤不應讓整個 backend 在 import 階段起不來。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_int_env(name: str, default: int) -> int:
    """讀取整數環境變數；格式錯誤時回退預設值，避免 import 階段中斷服務。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_model_artifact_root() -> Path:
    """取得模型 artifact 根目錄；未設定時固定落在專案 data 目錄。"""
    configured = os.getenv("MODEL_ARTIFACT_ROOT", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = PROJECT_ROOT / configured_path
        return configured_path.resolve()
    return (PROJECT_ROOT / "data" / "model_artifacts").resolve()


def get_imports_root() -> Path:
    """取得 WIPS 匯入上傳落地根目錄；未設定時固定落在專案 data/imports（container 內 /app/data/imports）。

    API 與 worker 共用同一份 root，用於落地路徑與 path traversal / 安全刪檔判斷。
    可用 IMPORTS_ROOT 環境變數覆蓋（相對路徑以專案根為基準）。
    """
    configured = os.getenv("IMPORTS_ROOT", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = PROJECT_ROOT / configured_path
        return configured_path.resolve()
    return (PROJECT_ROOT / "data" / "imports").resolve()


API_V1_PREFIX = "/api/v1"
WORKER_HEARTBEAT_TIMEOUT_SECONDS = get_int_env("WORKER_HEARTBEAT_TIMEOUT_SECONDS", 120)
# 匯入上傳容量上限（bytes），預設 200 MiB；串流累計超過即 413，即使 Content-Length 缺漏或偽造。
MAX_IMPORT_UPLOAD_BYTES = get_int_env("MAX_IMPORT_UPLOAD_BYTES", 200 * 1024 * 1024)
