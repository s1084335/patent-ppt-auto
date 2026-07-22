"""Patent Backend FastAPI 入口。

只負責建立 app 與掛載 route；不執行分群/報表等長時間工作（那些一律建 job
交 worker）。settings 先 import 以確保本機開發時 .env 已載入。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app import settings
from backend.app.api import (
    clustering,
    companion,
    comparison,
    events,
    imports,
    jobs,
    market,
    patents,
    reports,
    topics,
    workspaces,
)
from backend.app.repositories.topic_repository import TopicRepositoryUnavailableError

app = FastAPI(title="Patent Backend", version="0.1.0")
app.include_router(jobs.router, prefix=settings.API_V1_PREFIX)
app.include_router(clustering.router, prefix=settings.API_V1_PREFIX)
app.include_router(companion.router, prefix=settings.API_V1_PREFIX)
app.include_router(reports.router, prefix=settings.API_V1_PREFIX)
app.include_router(workspaces.router, prefix=settings.API_V1_PREFIX)
app.include_router(imports.router, prefix=settings.API_V1_PREFIX)
app.include_router(topics.router, prefix=settings.API_V1_PREFIX)
app.include_router(comparison.router, prefix=settings.API_V1_PREFIX)
app.include_router(events.router, prefix=settings.API_V1_PREFIX)
app.include_router(patents.router, prefix=settings.API_V1_PREFIX)
app.include_router(market.router, prefix=settings.API_V1_PREFIX)


_REPORT_LATEST = settings.PROJECT_ROOT / "output" / "full_report_latest" / "index.html"


@app.get("/api/v1/report-latest")
def serve_latest_report():
    """serve output/full_report_latest/index.html 如存在。"""
    from fastapi.responses import FileResponse, HTMLResponse

    if _REPORT_LATEST.exists():
        return FileResponse(str(_REPORT_LATEST))
    return HTMLResponse(
        content="<p>尚無報表產出。請先執行報表引擎產生 full_report_latest。</p>",
        status_code=404,
    )


@app.get("/")
def serve_frontend():
    """前端最小頁（單一 HTML + 原生 JS）。"""
    from pathlib import Path
    from fastapi.responses import HTMLResponse

    html_path = Path(__file__).resolve().parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.exception_handler(TopicRepositoryUnavailableError)
async def topic_repo_unavailable_handler(request: Request, exc: TopicRepositoryUnavailableError):
    """Repository 未配置時回傳 503。"""
    return JSONResponse(status_code=503, content={"detail": str(exc)})
