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


API_V1_PREFIX = "/api/v1"
WORKER_HEARTBEAT_TIMEOUT_SECONDS = get_int_env("WORKER_HEARTBEAT_TIMEOUT_SECONDS", 120)
