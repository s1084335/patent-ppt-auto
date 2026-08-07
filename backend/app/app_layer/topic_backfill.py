"""補分三段式的 DB 面（openspec change add-technical-channel-ai-backfill）。

第一段候選查詢、第二段建議讀取（建議本體隨 job result 落 workflow_outputs，
complete_job 自動存；⚠ analysis_outputs 是 legacy_0021 空表非現行落點）、
第三段批次核准寫入。
候選規則唯一定義處＝clustering/backfill.backfill_candidates，本檔只餵資料。
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.clustering.backfill import backfill_candidates
from backend.app.clustering.sources import PATENT_NOTE_SOURCE_COLUMNS, get_source_spec
from backend.app.db.connection import get_pool
from backend.app.repositories.topic_state_repository import PostgresTopicStateRepository

OUTPUT_TYPE = "topic_backfill_suggestion"
ASSIGNED_SOURCE_BACKFILL = "ai_backfill_approved"

# 補分輸入：文獻備註優先（當初就是為補分輸入設計），缺備註退三級 fallback 原文。
_INPUT_TEXT_EXPR = "COALESCE(" + ", ".join(
    ["""NULLIF(BTRIM(p."文獻備註"), '')"""]
    + [f"""NULLIF(BTRIM(p."{col}"), '')""" for col in PATENT_NOTE_SOURCE_COLUMNS]
) + ")"

_WS_MEMBER = """
EXISTS (
    SELECT 1 FROM app_layer.workspaces w
    JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
    WHERE w.workspace_id = %(workspace_id)s AND (m.pid)::bigint = p.id
)
"""

_ASSIGNED_SQL = """
SELECT DISTINCT ON (ta.patent_id) ta.patent_id
FROM derived_layer.topic_assignments ta
JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
WHERE wr.workspace_id = %(workspace_id)s AND tr.source_field = %(source_field)s
ORDER BY ta.patent_id, ta.run_id DESC
"""


def _fetch_assigned_ids(cur, workspace_id: int, source_field: str) -> set[int]:
    cur.execute(_ASSIGNED_SQL, {"workspace_id": workspace_id, "source_field": source_field})
    return {int(r["patent_id"]) for r in cur.fetchall()}


def fetch_candidates(workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """該通道補分候選（含補分輸入文本）；規則收斂在 backfill_candidates。"""
    col = get_source_spec(source_field).source_column
    sql = f"""
    SELECT p.id AS patent_id,
           NULLIF(BTRIM(p."申請號"), '') AS patent_number,
           p.title,
           p.document_kind,
           NULLIF(BTRIM(p."{col}"), '') AS source_text,
           {_INPUT_TEXT_EXPR} AS input_text
    FROM core_layer.patents p
    WHERE {_WS_MEMBER}
    ORDER BY p.id
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"workspace_id": workspace_id})
        rows = cur.fetchall()
        assigned = _fetch_assigned_ids(cur, workspace_id, source_field)
    return backfill_candidates(rows, assigned_patent_ids=assigned)


def fetch_topics(workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    """建議選單＝該通道現有主題（topic_code 即 assignments 的 topic_key）。"""
    state = PostgresTopicStateRepository().get_latest_topic_state(workspace_id, source_field)
    return [
        {"topic_key": t.get("topic_code"), "label": t.get("label") or "",
         "summary": t.get("summary") or ""}
        for t in state.get("topics", [])
        if t.get("topic_code")
    ]


def latest_suggestions(workspace_id: int, source_field: str) -> dict[str, Any]:
    """最新一批建議＋主題選單；已核准（已指派）者自動出清單。"""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT wo.data_json, wo.exported_at
            FROM app_layer.workflow_outputs wo
            JOIN app_layer.workflow_runs wr ON wr.run_id = wo.run_id
            WHERE wo.output_type = 'job_result:ai:topic_backfill'
              AND wr.workspace_id = %(workspace_id)s
              AND wo.data_json ->> 'source_field' = %(sf)s
            ORDER BY wo.exported_at DESC
            LIMIT 1
            """,
            {"sf": source_field, "workspace_id": workspace_id},
        )
        row = cur.fetchone()
        assigned = _fetch_assigned_ids(cur, workspace_id, source_field)
    if row is None:
        return {"suggestions": [], "topics": _safe_topics(workspace_id, source_field),
                "generated_at": None}
    data = row["data_json"] or {}
    pending = [s for s in data.get("suggestions", [])
               if int(s["patent_id"]) not in assigned]
    return {
        "suggestions": pending,
        "topics": _safe_topics(workspace_id, source_field),
        "ai_model": data.get("ai_model"),
        "prompt_version": data.get("prompt_version"),
        # complete_job 寫入的列 exported_at 可能為 NULL（匯出時才蓋章）。
        "generated_at": row["exported_at"].isoformat() if row["exported_at"] else None,
    }


def _safe_topics(workspace_id: int, source_field: str) -> list[dict[str, Any]]:
    try:
        return fetch_topics(workspace_id, source_field)
    except Exception:  # noqa: BLE001 - 尚未分群時選單為空，前端顯示提示
        return []


class TopicBackfillApprovalError(ValueError):
    """核准請求不合法（主題不在清單、專利已指派等）。"""


def approve_batch(
    workspace_id: int,
    source_field: str,
    approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    """第三段：批次核准→確定性寫入 topic_assignments（單一交易，部分失敗全回滾）。

    guard：topic_key 必須在現有主題清單；已指派者拒絕（不得重複寫）；
    寫入帶 assigned_source（CLU-015），run_id 掛該通道最新 run。
    不觸發任何 clustering／embedding 工作。
    """
    if not approvals:
        return {"approved": 0}
    state = PostgresTopicStateRepository().get_latest_topic_state(workspace_id, source_field)
    known = {t.get("topic_code") for t in state.get("topics", [])}
    run_id = int(state["run_id"])
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        assigned = _fetch_assigned_ids(cur, workspace_id, source_field)
        for item in approvals:
            pid = int(item["patent_id"])
            key = str(item["topic_key"])
            if key not in known:
                raise TopicBackfillApprovalError(f"topic_key {key!r} 不在現有主題清單")
            if pid in assigned:
                raise TopicBackfillApprovalError(f"patent {pid} 已有指派，不得重複核准")
        for item in approvals:
            cur.execute(
                """
                INSERT INTO derived_layer.topic_assignments
                    (run_id, workspace_id, patent_id, source_field, topic_key,
                     distance_to_centroid, assigned_source)
                VALUES (%s, %s, %s, %s, %s, NULL, %s)
                """,
                (run_id, workspace_id, int(item["patent_id"]),
                 source_field, str(item["topic_key"]), ASSIGNED_SOURCE_BACKFILL),
            )
        conn.commit()
    return {"approved": len(approvals), "run_id": run_id}
