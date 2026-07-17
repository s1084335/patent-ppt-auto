"""Patent MCP Server — reporting tools（純函式層）。

三支工具的實作，不 import mcp SDK：server.py 再把它們綁成 @mcp.tool()。
這樣單元測試不需要 mcp 依賴與傳輸層，直接呼叫函式即可驗證。

工具邊界：只回報表引擎／圖表引擎算好的確定性結果（數字、rows、圖檔路徑），
不做任何解讀——解讀與敘事是呼叫方（Claude Code）的責任。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.db.connection import get_connection_kwargs, get_pool
from backend.app.mcp_server._shared import json_safe
from backend.app.reports.chart_runner import fetch_analysis_patent_ids, run_chart_trial
from backend.app.reports.report_definitions import ALLOWED_FILTER_COLUMNS, REPORT_DEFINITIONS
from backend.app.reports.report_engine import run_reports_batch

# 回傳給 client 的預設列數上限：保護 LLM context 不被 detail 報表灌爆。
# 完整數據（各報表自己的預設列數）在出圖時落在 report_data.json。
DEFAULT_ROW_LIMIT = 50


def list_reports() -> dict[str, Any]:
    """列出可用報表目錄與篩選白名單——產報告前的探索入口。"""
    reports = [
        {
            "name": name,
            "label_zh": definition.label_zh,
            "label": definition.label,
            "report_type": definition.report_type,
            # patent_level＝篩選直接套在 patent 層；family_translated＝篩選先圈定
            # 「選中專利所屬家族」，再回這些家族的完整佈局（含家族全體成員）。
            "filter_mode": "patent_level" if definition.supports_patent_ids else "family_translated",
        }
        for name, definition in sorted(REPORT_DEFINITIONS.items())
    ]
    return {
        "reports": reports,
        "allowed_filter_columns": sorted(ALLOWED_FILTER_COLUMNS),
        "notes": [
            "filters 只能用 allowed_filter_columns 的欄位；值可為單值（等值）、list（IN）"
            '或 {"from": .., "to": .., "values": [..]}（區間／IN，鍵可任選）。',
            "filter_mode=family_translated 的家族層級報表：filters／快照先圈定「選中專利"
            "所屬家族」，再回這些家族的完整佈局（含家族全體成員，可能出現篩選外的國家）；"
            "不帶篩選＝全庫。",
        ],
    }


def run_report_analysis(
    report_names: list[str],
    filters: dict[str, Any] | None = None,
    limit: int | None = DEFAULT_ROW_LIMIT,
    with_charts: bool = True,
    analysis_id: int | None = None,
) -> dict[str, Any]:
    """跑指定報表：回數據（JSON rows），並（預設）為同一批報表選擇性出圖。

    - report_names：REPORT_DEFINITIONS 的 key（先用 list_reports 探索）；未知名稱直接報錯。
    - filters：報表引擎白名單篩選；家族層級報表轉譯成「選中專利所屬家族」的
      完整佈局並附 note（不帶篩選＝全庫）。
    - limit：回傳 rows 的列數上限（預設 50，保護 context）；出圖與 report_data.json
      用各報表自己的預設列數，不受此限。
    - with_charts=False：只回數據、不落任何檔（快速問數字用）。
    - analysis_id：數據與圖表都改用該 analysis 的專利快照，圖檔登錄 export_runs。

    數據與圖表出自同一套報表定義與篩選條件，口徑一致；圖表檔案在回傳的
    charts.output_dir 下，index.html 是彙整頁。
    """
    if not report_names:
        raise ValueError("report_names 不可為空（先用 list_reports 查可用報表）")
    unknown = sorted(set(report_names) - set(REPORT_DEFINITIONS))
    if unknown:
        raise ValueError(f"未知報表名：{', '.join(unknown)}（用 list_reports 查可用報表）")

    # analysis 快照只查一次，數據與圖表共用同一組 patent_ids 口徑。
    patent_ids = fetch_analysis_patent_ids(analysis_id) if analysis_id is not None else None

    data = run_reports_batch(list(report_names), filters=filters, limit=limit, patent_ids=patent_ids)
    result: dict[str, Any] = {
        "reports": data,
        "parameters": {
            "report_names": list(report_names),
            "filters": filters or None,
            "row_limit": limit,
            "with_charts": with_charts,
            "analysis_id": analysis_id,
        },
    }

    if with_charts:
        chart_result = run_chart_trial(
            analysis_id=analysis_id,
            report_names=list(report_names),
            filters=filters,
        )
        charts: dict[str, Any] = {
            "output_dir": chart_result["output_dir"],
            "index_html": str(Path(chart_result["output_dir"]) / "index.html"),
            "files": chart_result["files"],
            "sections_rendered": chart_result["sections_rendered"],
        }
        if "export_count" in chart_result:
            charts["export_count"] = chart_result["export_count"]
        result["charts"] = charts

    return json_safe(result)


def get_data_status() -> dict[str, Any]:
    """回報 DB 連線目標與資料量／新鮮度——產報告前的 sanity check。

    防呆重點：本機 5432 可能有一個空殼 PostgreSQL，環境變數沒帶對時連線會
    「成功但查到 0 筆」。這支工具讓呼叫方在跑報表前先確認連到的是有資料的庫、
    derived 層有刷新；warnings 非空時應先處理再出報表。
    """
    kwargs = get_connection_kwargs()
    target = {
        "host": kwargs.get("host", "(DATABASE_URL)"),
        "port": kwargs.get("port"),
        "dbname": kwargs.get("dbname"),
    }

    counts: dict[str, int] = {}
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            for key, count_sql in (
                ("patents", "SELECT COUNT(*) FROM core_layer.patents"),
                ("report_patent_base", "SELECT COUNT(*) FROM derived_layer.report_patent_base"),
                ("report_family_country", "SELECT COUNT(*) FROM derived_layer.report_family_country"),
                ("report_family_quality", "SELECT COUNT(*) FROM derived_layer.report_family_quality"),
            ):
                cur.execute(count_sql)
                counts[key] = int(cur.fetchone()[0])
            cur.execute("SELECT MAX(refreshed_at) FROM derived_layer.report_family_quality")
            family_refreshed_at = cur.fetchone()[0]

    warnings: list[str] = []
    if counts["patents"] == 0:
        warnings.append("core_layer.patents 是空的：可能連到空殼資料庫（檢查 PGPORT，開發庫在 5433）。")
    elif counts["report_patent_base"] == 0:
        warnings.append("derived_layer.report_patent_base 是空的：先跑 refresh_report_patent_base 再出報表。")
    elif counts["report_patent_base"] != counts["patents"]:
        warnings.append(
            "report_patent_base 筆數與 patents 不一致：derived 層可能未刷新（refresh_report_patent_base）。"
        )
    if counts["patents"] > 0 and counts["report_family_country"] == 0:
        warnings.append("report_family_country 是空的：家族佈局報表前先跑 refresh_report_family_country。")

    return json_safe(
        {
            "status": "warning" if warnings else "ok",
            "database": target,
            "row_counts": counts,
            "family_tables_refreshed_at": family_refreshed_at,
            "warnings": warnings,
        }
    )
