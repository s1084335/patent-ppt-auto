"""Market evidence workflow run repository。

Market evidence 需要外部 AI/CLI 找資料與人工確認，不應被一般 worker 認領。
此 repository 只建立可追溯的 `workflow_runs` 記錄，候選資料仍由
`workflow_outputs` 版本化保存。
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_pool


MARKET_EVIDENCE_RUN_TYPE = "market_evidence_research"
MARKET_EVIDENCE_TASK_STATUS = "waiting_external_research"


def create_market_evidence_run(
    *,
    task_payload: dict[str, Any],
    workspace_id: int | None = None,
) -> dict[str, Any]:
    """建立一筆 market evidence 追蹤 run，回傳 run_id 與目前狀態。"""
    if not isinstance(task_payload, dict):
        raise ValueError("task_payload must be a dict")
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO app_layer.workflow_runs
                    (run_type, status, workspace_id, request_json, worker_state_json)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING run_id, run_type, status, workspace_id, request_json
                """,
                (
                    MARKET_EVIDENCE_RUN_TYPE,
                    MARKET_EVIDENCE_TASK_STATUS,
                    workspace_id,
                    Jsonb(task_payload),
                    Jsonb({"current_stage": MARKET_EVIDENCE_TASK_STATUS, "progress_percent": 0}),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return {
        "run_id": int(row["run_id"]),
        "run_type": str(row["run_type"]),
        "status": str(row["status"]),
        "workspace_id": row["workspace_id"],
        "request_json": row["request_json"],
    }
