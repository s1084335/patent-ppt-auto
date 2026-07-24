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


def get_market_doc_root() -> Path:
    """取得市場資料 PDF 落地根目錄；未設定時落在專案 data 目錄。

    正式落點為 NAS（使用者定案），現階段先用本機目錄代替，換 NAS 只改環境變數
    MARKET_DOC_ROOT 不改程式。讀取者為本機 Companion 驅動的 CLI，故落檔案系統而非 DB。
    """
    configured = os.getenv("MARKET_DOC_ROOT", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = PROJECT_ROOT / configured_path
        return configured_path.resolve()
    return (PROJECT_ROOT / "data" / "market_docs").resolve()


API_V1_PREFIX = "/api/v1"
WORKER_HEARTBEAT_TIMEOUT_SECONDS = get_int_env("WORKER_HEARTBEAT_TIMEOUT_SECONDS", 120)
# 匯入上傳容量上限（bytes），預設 200 MiB；串流累計超過即 413，即使 Content-Length 缺漏或偽造。
MAX_IMPORT_UPLOAD_BYTES = get_int_env("MAX_IMPORT_UPLOAD_BYTES", 200 * 1024 * 1024)
