"""Patent Backend FastAPI 入口。

只負責建立 app 與掛載 route；不執行分群/報表等長時間工作（那些一律建 job
交 worker）。settings 先 import 以確保本機開發時 .env 已載入。
"""
from __future__ import annotations

from fastapi import FastAPI

from backend.app import settings
from backend.app.api import clustering, jobs, reports, workspaces

app = FastAPI(title="Patent Backend", version="0.1.0")
app.include_router(jobs.router, prefix=settings.API_V1_PREFIX)
app.include_router(clustering.router, prefix=settings.API_V1_PREFIX)
app.include_router(reports.router, prefix=settings.API_V1_PREFIX)
app.include_router(workspaces.router, prefix=settings.API_V1_PREFIX)
