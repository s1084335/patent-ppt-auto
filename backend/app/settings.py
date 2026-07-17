"""backend 統一設定：唯一載入 .env 的地方。

原則（patent-backend-worker-plan.md）：容器只讀環境變數，本機開發才載入專案
.env；不得讓各模組各自呼叫 load_dotenv()，避免誤連到 localhost:5432 的空殼庫。
其他模組要設定值一律 import 這裡。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/settings.py → 專案根為 parents[2]。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 本機開發載入 .env；override=False 讓容器已注入的環境變數優先，容器內無 .env 時為 no-op。
load_dotenv(PROJECT_ROOT / ".env", override=False)


# API 版本前綴。
API_V1_PREFIX = "/api/v1"

# worker 心跳逾時門檻（秒）：/ready 用它判斷 running job 是否 stale、worker 是否可能失聯。
WORKER_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("WORKER_HEARTBEAT_TIMEOUT_SECONDS", "120"))
