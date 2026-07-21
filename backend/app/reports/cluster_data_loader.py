"""0018 schema 分群資料載入（過渡用，0021 後改接 topic_state_repository）。

載入邏輯收在 load_cluster_workspace_data — 0021 後改接
backend/app/repositories/topic_state_repository.py（Claude 已完成，勿改它）。

compute_and_save_cluster_analysis 負責 載入→計算→寫入 app_layer.analysis_outputs。
"""
from __future__ import annotations

from typing import Any

from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.db.connection import get_connection_kwargs
from backend.app.reports.chart_runner import run_chart_trial
from backend.app.reports.cluster_analytics import (
    build_opportunity_matrix,
    build_pain_point_matrix,
    build_topic_effect_table,
)


def load_cluster_workspace_data(
    workspace_id: int,
    source_field: str,
    conn: Any,
) -> dict[str, Any]:
    """從 0018 schema 載入分群分析資料。

    0021 後改接 backend/app/repositories/topic_state_repository.py
    （Claude 已完成，勿改它）。

    Parameters
    ----------
    workspace_id : int
    source_field : str
    conn : psycopg connection（已連線，含 dict_row row_factory）。

    Returns
    -------
    dict
        topics : list[dict]
            每項含 topic_code / label / source_field。
        assignments : list[dict]
            每項含 topic_code / patent_id（int）。
        normalized_applicants : list[dict]
            每項含 patent_id / applicant_name。
        top_applicants_ws : list[str]
            該 workspace 主題涵蓋專利的前十大申請人名稱。
    """
    cur = conn.cursor()

    # ── 1. 讀取所有主題，建立 map 與合併鏈 ──
    cur.execute(
        "SELECT topic_id, topic_code, label, source_field, doc_count, "
        "       status, merged_into_topic_id "
        "FROM derived_layer.topics "
        "WHERE workspace_id = %s AND source_field = %s",
        (workspace_id, source_field),
    )
    all_topics = cur.fetchall()

    topic_by_id: dict[int, dict[str, Any]] = {}
    merged_target: dict[int, int] = {}
    active_topic_ids: list[int] = []
    for r in all_topics:
        tid = r["topic_id"]
        info = {
            "topic_code": r["topic_code"],
            "label": r["label"] or r["topic_code"],
            "source_field": r["source_field"] or source_field,
            "status": r["status"],
        }
        topic_by_id[tid] = info
        if r["status"] == "merged" and r["merged_into_topic_id"] is not None:
            merged_target[tid] = r["merged_into_topic_id"]
        elif r["status"] == "active":
            active_topic_ids.append(tid)

    def resolve_target(tid: int) -> int:
        seen: set[int] = set()
        while tid in merged_target and tid not in seen:
            seen.add(tid)
            tid = merged_target[tid]
        return tid

    # ── 2. 讀取指派，合併解析 ──
    cur.execute(
        "SELECT ta.patent_id, ta.topic_id, t.topic_code "
        "FROM derived_layer.topic_assignments ta "
        "JOIN derived_layer.topics t ON t.topic_id = ta.topic_id "
        "WHERE ta.workspace_id = %s AND ta.source_field = %s AND ta.is_current = true",
        (workspace_id, source_field),
    )
    raw_assignments = cur.fetchall()

    assignment_map: dict[str, set[int]] = {}
    for a in raw_assignments:
        target_id = resolve_target(a["topic_id"])
        if target_id not in topic_by_id:
            continue
        tc = topic_by_id[target_id]["topic_code"]
        assignment_map.setdefault(tc, set()).add(int(a["patent_id"]))

    assignments_out: list[dict[str, Any]] = []
    for tc, pids in assignment_map.items():
        for pid in sorted(pids):
            assignments_out.append({"topic_code": tc, "patent_id": pid})

    # 只有 active 的主題輸出
    topics_out: list[dict[str, Any]] = [
        topic_by_id[tid] for tid in active_topic_ids
    ]

    # ── 3. 讀取申請人名稱 ──
    all_patent_ids = list({
        int(a["patent_id"]) for a in raw_assignments
    })
    if all_patent_ids:
        cur.execute(
            "SELECT DISTINCT patent_id, applicant_display_name "
            "FROM derived_layer.report_patent_base "
            "WHERE patent_id = ANY(%s) AND applicant_display_name IS NOT NULL "
            "  AND applicant_display_name != ''",
            (all_patent_ids,),
        )
        applicant_rows = cur.fetchall()
    else:
        applicant_rows = []

    normalized_applicants: list[dict[str, Any]] = [
        {"patent_id": int(r["patent_id"]), "applicant_name": r["applicant_display_name"]}
        for r in applicant_rows
    ]

    # ── 4. 前十大申請人（跨 workspace 主題專利） ──
    if all_patent_ids:
        cur.execute(
            "SELECT applicant_display_name, COUNT(DISTINCT patent_id) AS cnt "
            "FROM derived_layer.report_patent_base "
            "WHERE patent_id = ANY(%s) AND applicant_display_name IS NOT NULL "
            "  AND applicant_display_name != '' "
            "GROUP BY applicant_display_name "
            "ORDER BY cnt DESC LIMIT 10",
            (all_patent_ids,),
        )
        top_rows = cur.fetchall()
    else:
        top_rows = []

    top_applicants_ws: list[str] = [r["applicant_display_name"] for r in top_rows]

    return {
        "topics": topics_out,
        "assignments": assignments_out,
        "normalized_applicants": normalized_applicants,
        "top_applicants_ws": top_applicants_ws,
    }


def compute_and_save_cluster_analysis(
    workspace_id: int,
    source_field: str,
    analysis_id: int,
    pain_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """載入 0018 schema → 計算三項分群分析 → 寫入 app_layer.analysis_outputs。

    0021 後改接 backend/app/repositories/topic_state_repository.py
    （Claude 已完成，勿改它）。

    Returns
    -------
    dict
        含 cluster_data 所有欄位，外加 topic_rows / opportunity_matrix /
        pain_point_matrix / analysis_status。
    """
    from psycopg.rows import dict_row

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row, connect_timeout=15) as conn:
        cluster_data = load_cluster_workspace_data(workspace_id, source_field, conn)

        if not cluster_data["topics"]:
            return {
                **cluster_data,
                "topic_rows": [],
                "opportunity_matrix": {},
                "pain_point_matrix": {},
                "analysis_status": "no_topics",
            }

        topic_rows = build_topic_effect_table(
            cluster_data["topics"],
            cluster_data["assignments"],
            cluster_data["normalized_applicants"],
        )
        opp_matrix = build_opportunity_matrix(
            topic_rows, cluster_data.get("top_applicants_ws", [])
        )
        pain_matrix = build_pain_point_matrix(
            topic_rows, pain_data or [],
            opp_matrix["patent_count_median"],
        )

        cur = conn.cursor()
        for output_name, result in [
            ("topic_effect_table", {"rows": topic_rows}),
            ("opportunity_matrix", opp_matrix),
            ("pain_point_matrix", pain_matrix),
        ]:
            cur.execute(
                "INSERT INTO app_layer.analysis_outputs "
                "    (analysis_id, output_type, output_name, result_json) "
                "VALUES (%s, 'cluster_analytics', %s, %s)",
                (analysis_id, output_name, Jsonb(result)),
            )
        conn.commit()

    return {
        **cluster_data,
        "topic_rows": topic_rows,
        "opportunity_matrix": opp_matrix,
        "pain_point_matrix": pain_matrix,
        "analysis_status": "saved",
    }


def run_full_report(
    workspace_id: int,
    source_field: str,
    analysis_id: int,
    pain_data: list[dict[str, Any]] | None = None,
    output_dir: str | None = None,
    # 排名類顯示與保存一律前 20（2026-07-21 定案修正，與 run_chart_trial 預設一致）
    ranking_limit: int = 20,
) -> dict[str, Any]:
    """完整報表輸出：先算分群分析入庫，再出全套圖表（含分群區塊）。

    出圖目錄：output/full_report_latest/report_trial_{timestamp}/，
    含 artifact_manifest.json（SHA-256），不覆蓋既有版本。

    0021 後改接 backend/app/repositories/topic_state_repository.py
    （Claude 已完成，勿改它）。
    """
    cluster_result = compute_and_save_cluster_analysis(
        workspace_id=workspace_id,
        source_field=source_field,
        analysis_id=analysis_id,
        pain_data=pain_data,
    )

    cluster_data_for_charts = (
        cluster_result if cluster_result["analysis_status"] == "saved" else None
    )

    chart_result = run_chart_trial(
        output_dir=Path(output_dir) if output_dir else None,
        ranking_limit=ranking_limit,
        analysis_id=analysis_id,
        report_names=None,  # 出整套
        cluster_data=cluster_data_for_charts,
    )

    return {
        **chart_result,
        "analysis_status": cluster_result["analysis_status"],
    }
