"""0021 schema 分群資料載入：topics/assignments 委派 topic_state_repository。

load_cluster_workspace_data 走 PostgresTopicStateRepository（唯一事實來源，已含
合併重映與 incremental fallback），applicant 由 derived_layer.report_patent_base 取；
不再直查 0018 derived_layer.topics（0021 已不存在）。

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
from backend.app.transforms.patent_numbers import display_number_sql
from backend.app.reports.cluster_analytics import (
    build_opportunity_matrix,
    build_topic_effect_table,
)
from backend.app.repositories.topic_state_repository import (
    PostgresTopicStateRepository,
    TopicStateNotFoundError,
)


def load_cluster_workspace_data(
    workspace_id: int,
    source_field: str,
    conn: Any,
) -> dict[str, Any]:
    """從 0021 schema 載入分群分析資料。

    topics/assignments 委派 backend/app/repositories/topic_state_repository.py
    （唯一事實來源，已含合併重映、未分類保留與 incremental fallback）；applicant
    名稱由本函式以傳入 conn 查 derived_layer.report_patent_base。

    Parameters
    ----------
    workspace_id : int
    source_field : str
    conn : psycopg connection（已連線，含 dict_row row_factory）；供 applicant 查詢。

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
        patents : dict[int, dict]
            patent_id → {application_year, number, title}；供技術狀態分類與
            代表專利使用（與申請人同一查詢帶回，不多走一趟 DB）。
    """
    # ── 1+2. topics 與 assignments 委派唯讀 repository（合併鏈、未分類、incremental
    #         fallback 均在 repository 內處理；無 topics 的 run 視為空結果） ──
    try:
        state = PostgresTopicStateRepository().get_latest_topic_state(
            workspace_id, source_field)
    except TopicStateNotFoundError:
        return {
            "topics": [], "assignments": [],
            "normalized_applicants": [], "top_applicants_ws": [],
            "patents": {},
            "topic_run_id": None, "topic_state_version": None,
        }

    topics_out: list[dict[str, Any]] = [
        {
            "topic_code": t["topic_code"],
            "label": t.get("label") or t["topic_code"],
            "source_field": source_field,
            "status": "active",
        }
        for t in state["topics"]
    ]
    assignments_out: list[dict[str, Any]] = []
    for t in state["topics"]:
        for pid in t["patent_ids"]:
            assignments_out.append({"topic_code": t["topic_code"], "patent_id": int(pid)})

    # ── 3. 讀取申請人名稱 ──
    cur = conn.cursor()
    all_patent_ids = sorted({a["patent_id"] for a in assignments_out})
    if all_patent_ids:
        # ⚠ 申請年、專利號、名稱併進**同一個查詢**帶回：技術狀態分類與代表專利
        # 都要用，另開查詢等於為同一批 patent_id 走兩三趟 DB。
        # ⚠ 「文獻備註」在 `core_layer.patents`，**不在** report_patent_base
        # （後者是 legacy_0021 的相容 VIEW；0039 加的 abstract 是產生備註的輸入，
        # 不是備註本身）。故 LEFT JOIN 取回——取不到時為 NULL，代表專利只顯示號碼。
        # 🔴 2026-08-04 治本：專利號走 display_number_sql 唯一定義處。
        # 原本單取原值公開號——TW 案顯示西元前綴（202421229 而非扣 1911 的
        # 11321229）、M 開頭授權案（公開號 NULL）代表專利直接空白。
        cur.execute(
            f'SELECT DISTINCT b.patent_id, b.applicant_display_name, b.application_year, '
            f'       {display_number_sql("b")} AS patent_number, b.title, '
            f'       p."文獻備註" AS patent_note '
            "FROM derived_layer.report_patent_base b "
            "LEFT JOIN core_layer.patents p ON p.id = b.patent_id "
            "WHERE b.patent_id = ANY(%s) AND b.applicant_display_name IS NOT NULL "
            "  AND b.applicant_display_name != ''",
            (all_patent_ids,),
        )
        applicant_rows = cur.fetchall()
    else:
        applicant_rows = []

    normalized_applicants: list[dict[str, Any]] = [
        {"patent_id": int(r["patent_id"]), "applicant_name": r["applicant_display_name"]}
        for r in applicant_rows
    ]
    # patent_id → 該專利屬性（申請年／專利號／名稱）。⚠ 單一入口：狀態分類與
    # 代表專利都從這裡拿，不另建第二份 patent_id 對照表。
    # 缺年份的專利仍收進來（代表專利用得到），但 `_window_metrics` 不會把它算進任一窗。
    patents: dict[int, dict[str, Any]] = {
        int(r["patent_id"]): {
            "application_year": r.get("application_year"),
            "number": r.get("patent_number") or "",
            "title": r.get("title") or "",
            "note": r.get("patent_note") or "",
        }
        for r in applicant_rows
    }

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
        "patents": patents,
        # #3b（2026-08-05）：把 repository 已查到的版本帶出來供報表落章。
        # ⚠ 不多查一趟 DB——`get_latest_topic_state` 回傳裡本來就有這兩個值，
        # 只是先前沒往外傳，於是報表無從記錄自己用的是哪一版主題。
        "topic_run_id": state.get("run_id"),
        "topic_state_version": state.get("state_run_id"),
    }


def compute_and_save_cluster_analysis(
    workspace_id: int,
    source_field: str,
    analysis_id: int,
) -> dict[str, Any]:
    """載入 0018 schema → 計算三項分群分析 → 寫入 app_layer.analysis_outputs。

    0021 後改接 backend/app/repositories/topic_state_repository.py
    （Claude 已完成，勿改它）。

    Returns
    -------
    dict
        含 cluster_data 所有欄位，外加 topic_rows / opportunity_matrix /
        analysis_status。
    """
    from psycopg.rows import dict_row

    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row, connect_timeout=15) as conn:
        cluster_data = load_cluster_workspace_data(workspace_id, source_field, conn)

        if not cluster_data["topics"]:
            return {
                **cluster_data,
                "topic_rows": [],
                "opportunity_matrix": {},
                "analysis_status": "no_topics",
            }

        topic_rows = build_topic_effect_table(
            cluster_data["topics"],
            cluster_data["assignments"],
            cluster_data["normalized_applicants"],
            patents=cluster_data.get("patents"),
        )
        opp_matrix = build_opportunity_matrix(
            topic_rows, cluster_data.get("top_applicants_ws", [])
        )
        cur = conn.cursor()
        for output_name, result in [
            ("topic_effect_table", {"rows": topic_rows}),
            ("opportunity_matrix", opp_matrix),
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
        "analysis_status": "saved",
    }


def run_full_report(
    workspace_id: int,
    source_field: str,
    analysis_id: int,
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
