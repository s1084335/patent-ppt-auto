"""SSE events + tasks list + Companion 狀態 API。

轉承 decisions.md「AI 通道維持原架構＋SSE」「長時任務前端進度顯示」兩節。

⚠ 原本這裡另有一支 `POST /ai-tasks`，與 `companion.py` 的建任務端點語意重疊；
已整併到 `backend/app/api/ai_tasks.py`（該端點需 bearer token），本檔不再提供
建任務入口，只保留唯讀的 tasks 列表、SSE 與 Companion 狀態查詢。

`GET /companion/status` 放在本檔而非 `ai_tasks.py`：它是唯讀的觀測端點，
與 SSE／tasks 同屬「前端拿現況」這一類；`ai_tasks.py` 的 router 綁死
`/ai-tasks` prefix，掛不了 `/companion` 路徑，另開 router 就必須改 main.py。

Windows ProactorEventLoop 與 psycopg async 不相容，SSE LISTEN 採
thread ＋ psycopg sync notifies(timeout=0.5) 輪詢 ＋ asyncio.Queue 遞送。
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.app.api._auth import require_api_token
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


@router.get("/companion/status", dependencies=[Depends(require_api_token)])
def companion_status() -> dict[str, Any]:
    """回報本機 Patent Companion 的存活狀態，供前端狀態燈顯示。

    資料來源是 Companion 落在 `<AI_BRIDGE_STATE_DIR>/ai_bridge_heartbeat.json`
    的心跳檔，判準沿用 `ai_bridge.read_heartbeat`（ok／stale／stopped／missing／
    unreadable），前端與 launcher 才不會出現兩套判活邏輯。

    ⚠ 跨容器限制（預期行為，非 bug）：heartbeat 是**使用者本機的檔案**。
    Companion 依架構定案必須跑在使用者電腦上（要用其 Claude CLI 與登入 token），
    而部署在 Railway 等雲端的 backend 與它不共用檔案系統，**永遠讀不到**這個檔，
    因此雲端上的本端點固定回 `state="missing"`。

    為了讓前端能區分「Companion 真的沒跑」與「這個 backend 根本讀不到本機心跳」，
    回應帶三個欄位：
    - `heartbeat_readable`：是否成功讀到心跳檔（`missing`／`unreadable` 為 false）。
    - `backend_is_local`：本 backend 是否與 Companion 同機（沒有雲端部署跡象即視為本機）。
      `heartbeat_readable=false` 且 `backend_is_local=false` ⇒ 應顯示「無法從此伺服器
      判斷 Companion 狀態」，而不是「Companion 沒在跑」。
    - `note`：對應上述情境的繁中說明，前端可直接顯示。
    """
    from backend.app.worker import ai_bridge

    heartbeat = ai_bridge.read_heartbeat()
    state = str(heartbeat.get("reason", "missing"))
    readable = state not in {"missing", "unreadable"}
    backend_is_local = _backend_runs_locally()

    age_seconds = heartbeat.get("age_seconds")
    last_heartbeat_at: str | None = None
    if age_seconds is not None:
        last_heartbeat_at = (
            datetime.now(UTC) - timedelta(seconds=float(age_seconds))
        ).isoformat()

    if readable:
        note = {
            "ok": "Companion 正在執行。",
            "stale": "Companion 心跳過舊，可能已當掉；請重新點擊桌面捷徑。",
            "stopped": "Companion 已正常關閉；請重新點擊桌面捷徑啟動。",
        }.get(state, "Companion 狀態未知。")
    elif backend_is_local:
        note = "找不到本機 Companion 心跳檔，Companion 尚未啟動過；請點擊桌面捷徑。"
    else:
        note = (
            "此伺服器與 Companion 不在同一台機器，讀不到本機心跳檔，"
            "無法由此判斷 Companion 是否在執行。"
        )

    return {
        "state": state,
        "alive": bool(heartbeat.get("alive", False)),
        "heartbeat_readable": readable,
        "backend_is_local": backend_is_local,
        "last_heartbeat_at": last_heartbeat_at,
        "age_seconds": age_seconds,
        "worker_id": heartbeat.get("worker_id"),
        "pid": heartbeat.get("pid"),
        "heartbeat_path": heartbeat.get("path"),
        "note": note,
    }


def _backend_runs_locally() -> bool:
    """判斷本 backend 是否與使用者的 Companion 同機（決定 missing 該怎麼解讀）。

    用「有沒有雲端部署跡象」反推，而不是列舉本機情境：Railway／容器部署會設下列
    環境變數之一，本機開發則都沒有。判斷錯的代價只是 note 文案不同，不影響 state。
    """
    import os

    cloud_markers = ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "KUBERNETES_SERVICE_HOST")
    return not any(os.getenv(name) for name in cloud_markers)
