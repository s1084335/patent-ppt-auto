"""SSE events + tasks list API。

轉承 decisions.md「AI 通道維持原架構＋SSE」「長時任務前端進度顯示」兩節。

⚠ 原本這裡另有一支 `POST /ai-tasks`，與 `companion.py` 的建任務端點語意重疊；
已整併到 `backend/app/api/ai_tasks.py`（該端點需 bearer token），本檔不再提供
建任務入口，只保留唯讀的 tasks 列表與 SSE。

Windows ProactorEventLoop 與 psycopg async 不相容，SSE LISTEN 採
thread ＋ psycopg sync notifies(timeout=0.5) 輪詢 ＋ asyncio.Queue 遞送。
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.app.api.jobs import job_to_dict
from backend.app.db import job_repository as jr
from backend.app.db.connection import get_connection_kwargs


router = APIRouter(tags=["events"])


@router.get("/tasks")
def list_tasks(limit: int = 20) -> dict[str, Any]:
    if limit < 1:
        raise HTTPException(status_code=422, detail="limit must be >= 1")
    jobs = jr.list_jobs(limit=limit)
    return {"tasks": [job_to_dict(j) for j in jobs]}


@router.get("/events")
async def sse_events(request: Request):
    """SSE endpoint — thread LISTEN patent_events、asyncio.Queue fanout、心跳 15s。"""

    async def event_generator():
        kwargs = get_connection_kwargs()
        loop = asyncio.get_event_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stop = threading.Event()

        def listen_worker():
            conn = psycopg.connect(**kwargs)
            try:
                conn.execute("LISTEN patent_events")
                conn.commit()
                for notify in conn.notifies(timeout=0.5):
                    if stop.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(
                        q.put(json.loads(notify.payload)), loop
                    )
            finally:
                conn.close()

        t = threading.Thread(target=listen_worker, daemon=True)
        t.start()

        try:
            while True:
                if await request.is_disconnected():
                    stop.set()
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: heartbeat\ndata: \n\n"
        finally:
            stop.set()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
