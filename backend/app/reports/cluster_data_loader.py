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
from backend.app.reports.report_engine import (
    REPORT_SCOPE_COMPANY,
    scoped_column,
    scoped_source_table,
)
from backend.app.transforms.patent_numbers import display_number_sql
from backend.app.reports.cluster_analytics import (
    build_opportunity_matrix,
    build_topic_effect_table,
)
from backend.app.repositories.topic_state_repository import (
    PostgresTopicStateRepository,
    TopicStateNotFoundError,
)

# 申請人來源（未套 scope 的原形）。實際查詢時一律經 `scoped_source_table`／
# `scoped_column` 換算——集團歸戶的對照只能有一個定義處（`report_engine`），
# 這裡若自己寫死 `_with_groups` 就會變成第二份、日後各自漂移。
# ⚠ 用**展開 VIEW**（申請人粒度，共同申請一件兩列），與 `applicant_ranking`／
#   `applicant_year_matrix` 同源；原本讀 `report_patent_base` 是專利粒度且未歸集團。
APPLICANT_SOURCE_TABLE = "derived_layer.report_patent_applicant_expanded"
APPLICANT_NAME_COLUMN = "applicant_display_name"


def load_cluster_workspace_data(
    workspace_id: int,
    source_field: str,
    conn: Any,
    report_scope: str = REPORT_SCOPE_COMPANY,
) -> dict[str, Any]:
    """從 0021 schema 載入分群分析資料。

    topics/assignments 委派 backend/app/repositories/topic_state_repository.py
    （唯一事實來源，已含合併重映、未分類保留與 incremental fallback）；applicant
    名稱由本函式以傳入 conn 查申請人展開 VIEW（依 report_scope 換算集團欄）。

    Parameters
    ----------
    workspace_id : int
    source_field : str
    conn : psycopg connection（已連線，含 dict_row row_factory）；供 applicant 查詢。
    report_scope : str
        `company`／`group`。🔴 2026-08-20 補：**必須與同一份報表其餘各表同值**。
        原本本函式固定讀未歸集團的名稱，而 `applicant_ranking` 等表依 scope 走
        集團欄——同一份報表對同一個主題給出兩種答案（實測技術 T001「最大一家」
        16% vs 35%，一個讀作「沒有壟斷者」、一個讀作「一家佔三分之一」）。
        換算一律走 `report_engine` 的 `scoped_*`，不在此另備一份對照。

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
    # scope 換算集中在這兩行，下面兩個查詢共用——換算規則本身在 report_engine。
    applicant_view = scoped_source_table(APPLICANT_SOURCE_TABLE, report_scope)
    applicant_col = scoped_column(APPLICANT_NAME_COLUMN, report_scope)
    if all_patent_ids:
        # ⚠ 申請年、專利號、名稱併進**同一個查詢**帶回：技術狀態分類與代表專利
        # 都要用，另開查詢等於為同一批 patent_id 走兩三趟 DB。
        # ⚠ 「文獻備註」在 `core_layer.patents`，**不在** report_patent_base
        # （後者是 legacy_0021 的相容 VIEW；0039 加的 abstract 是產生備註的輸入，
        # 不是備註本身）。故 LEFT JOIN 取回——取不到時為 NULL，代表專利只顯示號碼。
        # 🔴 2026-08-04 治本：專利號走 display_number_sql 唯一定義處。
        # 原本單取原值公開號——TW 案顯示西元前綴（202421229 而非扣 1911 的
        # 11321229）、M 開頭授權案（公開號 NULL）代表專利直接空白。
        # 🔴 2026-08-20：申請人改由**申請人展開 VIEW**取，並依 report_scope 換算。
        # 原本讀 `report_patent_base.applicant_display_name`——那是專利粒度且
        # 固定未歸集團，而 `applicant_ranking`／`applicant_year_matrix`／設計保護
        # 策略都依 scope 走集團欄，於是同一份報表對同一個主題給出兩種答案：
        # 實測技術 T001「最大一家」16%（未歸集團）vs 35%（集團）——一個讀作
        # 「沒有壟斷者」、一個讀作「一家佔三分之一」，方向相反。30 個主題中
        # 15 個受影響，並傳到 `derive_thresholds` 的中位數與象限切線。
        # ⚠ 展開 VIEW 是**申請人粒度**（共同申請一件兩列），patent 屬性仍由
        #   `report_patent_base` 供給——兩種粒度 JOIN 在同一趟，不多走一次 DB。
        cur.execute(
            f'SELECT DISTINCT a."{applicant_col}" AS applicant_name, '
            f'       b.patent_id, b.application_year, '
            f'       {display_number_sql("b")} AS patent_number, b.title, '
            # 法律狀態（2026-08-18，§7e）：供主題表的狀態分解與結論頁排序用。
            # ⚠ 併進這一趟查詢，不另開一趟——loader 註解明訂 patents 是單一入口，
            #   多一趟就是第二份 patent 對照表。桶收斂在 Python 端走
            #   mappings/legal_status 唯一定義處，SQL 只回原值。
            f'       b.legal_status AS legal_status, '
            f'       p."文獻備註" AS patent_note '
            f"FROM {applicant_view} a "
            "JOIN derived_layer.report_patent_base b ON b.patent_id = a.patent_id "
            "LEFT JOIN core_layer.patents p ON p.id = a.patent_id "
            f'WHERE a.patent_id = ANY(%s) AND a."{applicant_col}" IS NOT NULL '
            f'  AND a."{applicant_col}" != \'\'',
            (all_patent_ids,),
        )
        applicant_rows = cur.fetchall()
    else:
        applicant_rows = []

    normalized_applicants: list[dict[str, Any]] = [
        {"patent_id": int(r["patent_id"]), "applicant_name": r["applicant_name"]}
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
            # 原值不收斂——桶定義只能有一個定義處（mappings/legal_status）。
            "legal_status": r.get("legal_status"),
        }
        for r in applicant_rows
    }

    # ── 4. 前十大申請人（跨 workspace 主題專利） ──
    if all_patent_ids:
        # ⚠ 與上面同口徑：前十大是象限「龍頭涉入」的判定依據，兩處用不同名稱
        #   會出現「排名前三的公司在象限裡不算龍頭」這種對不起來的結果。
        cur.execute(
            f'SELECT "{applicant_col}" AS applicant_name, '
            "       COUNT(DISTINCT patent_id) AS cnt "
            f"FROM {applicant_view} "
            f'WHERE patent_id = ANY(%s) AND "{applicant_col}" IS NOT NULL '
            f'  AND "{applicant_col}" != \'\' '
            f'GROUP BY "{applicant_col}" '
            # ⚠ 2026-08-20 補次要排序：原本只有 `cnt DESC`，同件數者順序由
            #   Postgres 自行決定——同一批資料重跑可能得到不同的前十大。實測割草機
            #   第 9／10 名（PELLENC 與 浙江動一，各 4 件）就會互換。名次本身無傷，
            #   但 `top_applicants_ws` 是象限「龍頭涉入」的判定依據：若第 10 名有
            #   三家平手，誰進榜就會逐次變動，而報表看起來完全正常。
            f'ORDER BY cnt DESC, "{applicant_col}" LIMIT 10',
            (all_patent_ids,),
        )
        top_rows = cur.fetchall()
    else:
        top_rows = []

    top_applicants_ws: list[str] = [r["applicant_name"] for r in top_rows]

    # CLU-016（補分 change）：標記人工核准之 AI 補分件，供報表母體註記分計。
    # 0048 之前的 DB 無 assigned_source 欄——查詢失敗視為全幾何（count 0 不出註記）。
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT ta.patent_id "
            "FROM derived_layer.topic_assignments ta "
            "JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id "
            "JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id "
            "WHERE wr.workspace_id = %s AND tr.source_field = %s "
            "  AND ta.assigned_source = 'ai_backfill_approved'",
            (workspace_id, source_field),
        )
        backfill_ids = {int(r["patent_id"]) for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        backfill_ids = set()
    for a in assignments_out:
        a["assigned_source"] = (
            "ai_backfill_approved" if a["patent_id"] in backfill_ids else "geometric"
        )

    # 申請人四面向的來源列（KP 象限引擎端配套，2026-08-07）：展開 VIEW（共同申請
    # 各自計數）JOIN patents 取國別／同族／狀態／種類，再 LATERAL 取該通道主題。
    # ⚠ 一次查完：四面向要的五樣東西分開查就是四趟 DB。
    # ⚠ 母體＝**該 workspace 全部專利**，不是有主題指派的那些——布局量要算
    # 設計案等未分群件（2026-08-07 真資料抓到：只取 44 件會讓曾晴少算 1 件、
    # 帝瑪斯少算 2 件，與排名頁 55 件口徑對不上）。主題數則自然只計有指派者。
    # 🔴 2026-08-20 晚場：申請人名稱改走 scope 換算（原本寫死未歸集團的欄與 VIEW）。
    # 實測割草機：該表 10 列與**原始名口徑 10/10 相符、與集團口徑僅 6/10**——
    # 排名頁合併成「創科 44 件」，這裡拆成創科 16／美沃奇 21／Chuang Ke Limited 5。
    # ⚠ 這是本檔第二個同型缺陷（第一個是 `normalized_applicants`／`top_applicants_ws`，
    #   同日稍早已修）。修 bug 時要問「這個錯誤假設還有誰也在用」——當時沒問到這裡。
    # ⚠ alias 回 `applicant_display_name`：下游 `content_blocks.key_player_profiles`
    #   只認這個欄名，換來源不該連帶改動消費端。
    try:
        cur = conn.cursor()
        cur.execute(
            f'SELECT e."{applicant_col}" AS applicant_display_name, '
            '       e.patent_id, e.application_year, '
            '       e.country_code, p."WIPS同族ID" AS family_id, p.legal_status, '
            '       p.patent_type, p.document_kind, '
            '       LEFT(NULLIF(BTRIM(p."Orig. IPC(Main)"), \'\'), 4) AS ipc_subclass, '
            '       ta.topic_key '
            f'FROM {applicant_view} e '
            'JOIN core_layer.patents p ON p.id = e.patent_id '
            'LEFT JOIN LATERAL ('
            '    SELECT ta.topic_key FROM derived_layer.topic_assignments ta '
            '    JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id '
            '    JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id '
            '    WHERE ta.patent_id = e.patent_id AND tr.source_field = %s '
            '      AND wr.workspace_id = %s '
            '    ORDER BY ta.run_id DESC LIMIT 1'
            ') ta ON TRUE '
            'WHERE EXISTS ('
            '    SELECT 1 FROM app_layer.workspaces w '
            '    JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE '
            '    WHERE w.workspace_id = %s AND (m.pid)::bigint = e.patent_id'
            ')',
            (source_field, workspace_id, workspace_id),
        )
        strength_rows = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 - 四面向缺了不該讓整份報表產不出來
        strength_rows = []

    return {
        "topics": topics_out,
        "assignments": assignments_out,
        "normalized_applicants": normalized_applicants,
        "top_applicants_ws": top_applicants_ws,
        "patents": patents,
        # #3b（2026-08-05）：把 repository 已查到的版本帶出來供報表落章。
        # ⚠ 不多查一趟 DB——`get_latest_topic_state` 回傳裡本來就有這兩個值，
        # 只是先前沒往外傳，於是報表無從記錄自己用的是哪一版主題。
        "strength_rows": strength_rows,
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
