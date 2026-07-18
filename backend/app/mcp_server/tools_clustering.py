"""Patent MCP Server — clustering tools（純函式層，輕量七支）。

包 backend.app.clustering.workspace_service 的唯讀 payload／輕量寫回介面，
供 Claude Code 做主題標籤、摘要與分群結果解讀。重負載操作（calibrate／
finalize／incremental／merge／unmerge／embedding）不進 MCP——那些是使用者
從 Web 觸發的精確計算，走 Web→FastAPI（clustering/api.py）。

邊界：工具只回分群引擎的確定性結果；Claude 產的 label/summary 經
apply_topic_labels 寫回時只更新非人工定案（label_source<>'manual'）的
topics，正式定案權在使用者。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.app.clustering import workspace_service
from backend.app.clustering.sources import source_fields
from backend.app.db.connection import get_connection_kwargs
from backend.app.mcp_server._shared import json_safe


def list_workspaces() -> dict[str, Any]:
    """列出 active workspaces 與專利件數（分群探索入口）。

    與 clustering/api.py 的 GET /api/workspaces 同一查詢——服務層沒有這支
    函式，MCP 不 import HTTP 層（避免把 FastAPI app 拖進 MCP process），
    故在此保留同款 SQL；兩邊若改要一起改。
    """
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT w.workspace_id, w.workspace_name, w.description, w.status,
                   count(wp.patent_id)::integer AS patent_count,
                   w.created_at, w.updated_at
            FROM app_layer.workspaces w
            LEFT JOIN app_layer.workspace_patents wp ON wp.workspace_id = w.workspace_id
            WHERE w.status = 'active'
            GROUP BY w.workspace_id
            ORDER BY w.updated_at DESC, w.workspace_id DESC
            """
        ).fetchall()
    return json_safe(
        {
            "workspaces": [dict(row) for row in rows],
            # 兩個分群通道（技術／功效）的 source_field；後續工具都要指定其一。
            "source_fields": list(source_fields()),
        }
    )


def get_workspace_dashboard(workspace_id: int) -> dict[str, Any]:
    """單一 workspace 的完整儀表板：workspace、雙通道 topics、專利列表。"""
    return json_safe(workspace_service.workspace_dashboard(int(workspace_id)))


def get_candidate_review_payload(run_id: int) -> dict[str, Any]:
    """取 calibrate 產生的候選主題數方案（k 掃描結果），供 Claude 產生候選說明。"""
    return json_safe(workspace_service.candidate_review_payload(int(run_id)))


def apply_candidate_explanations(
    run_id: int,
    explanations: list[dict[str, Any]],
) -> dict[str, Any]:
    """寫回 Claude 對候選主題數方案的差異說明（只存說明，不代使用者選案）。

    explanations：[{candidate_id, explanation}, ...]；空白說明或超過硬上限
    會被拒絕。回傳 requested_count/updated_count，兩者不一致代表有
    candidate_id 不屬於此 run。候選定案仍由使用者在前端 finalize。
    """
    return json_safe(
        workspace_service.apply_candidate_explanations(
            run_id=int(run_id),
            explanations=explanations,
        )
    )


def get_topic_labeling_payload(
    workspace_id: int,
    source_field: str,
    topic_ids: list[int] | None = None,
) -> dict[str, Any]:
    """取 topics 的關鍵字＋代表專利 payload，供 Claude 產生 label/summary。

    topic_ids 不給＝該 workspace／source_field 的全部 active topics。
    """
    return json_safe(
        workspace_service.topic_labeling_payload(
            workspace_id=int(workspace_id),
            source_field=source_field,
            topic_ids=[int(t) for t in topic_ids] if topic_ids else None,
        )
    )


def apply_topic_labels(
    workspace_id: int,
    source_field: str,
    labels: list[dict[str, Any]],
    updated_by: str = "claude-code",
) -> dict[str, Any]:
    """寫回 Claude 產的 topic label/summary。

    labels：[{topic_id, label, summary?, source?}, ...]；label 不可為空。
    只更新 label_source<>'manual' 的 active topics——人工定案不被 AI 覆蓋。
    """
    return json_safe(
        workspace_service.apply_topic_labels(
            workspace_id=int(workspace_id),
            source_field=source_field,
            labels=labels,
            updated_by=updated_by,
        )
    )


def get_merge_history(workspace_id: int, source_field: str) -> dict[str, Any]:
    """列出主題合併歷史與可否獨立復原（merge/unmerge 本身由 Web→FastAPI 執行，MCP 只讀）。"""
    return json_safe(
        {
            "merge_history": workspace_service.merge_history(
                workspace_id=int(workspace_id), source_field=source_field
            )
        }
    )
