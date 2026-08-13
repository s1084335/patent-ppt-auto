"""Patent MCP Server — reporting tools（純函式層）。

三支工具的實作，不 import mcp SDK：server.py 再把它們綁成 @mcp.tool()。
這樣單元測試不需要 mcp 依賴與傳輸層，直接呼叫函式即可驗證。

工具邊界：只回報表引擎／圖表引擎算好的確定性結果（數字、rows、圖檔路徑），
不做任何解讀——解讀與敘事是呼叫方（Claude Code）的責任。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.db.connection import get_connection_kwargs, get_pool
from backend.app.mcp_server._shared import json_safe
from backend.app.reports.chart_runner import fetch_analysis_patent_ids, run_chart_trial
from backend.app.reports.report_definitions import (
    ALLOWED_FILTER_COLUMNS,
    DEFAULT_REPORT_NAMES,
    REPORT_DEFINITIONS,
)
from backend.app.reports.report_engine import run_reports_batch

# 回傳給 client 的預設列數上限：保護 LLM context 不被 detail 報表灌爆。
# 完整數據（各報表自己的預設列數）在出圖時落在 report_data.json。
DEFAULT_ROW_LIMIT = 50


def _load_report_data_json(output_dir: str | Path) -> dict[str, Any]:
    """讀取本次報表頁面對應的結構化資料，作為 workflow_outputs 的回存 payload。"""
    report_data_path = Path(output_dir) / "report_data.json"
    payload = json.loads(report_data_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{report_data_path} must contain a JSON object")
    return payload


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
        "default_report_names": list(DEFAULT_REPORT_NAMES),
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
    report_names: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int | None = DEFAULT_ROW_LIMIT,
    with_charts: bool = True,
    analysis_id: int | None = None,
) -> dict[str, Any]:
    """跑指定報表：回數據（JSON rows），並（預設）為同一批報表選擇性出圖。

    - report_names：REPORT_DEFINITIONS 的 key；None 或 [] 會使用固定預設報表組合。
    - filters：報表引擎白名單篩選；家族層級報表轉譯成「選中專利所屬家族」的
      完整佈局並附 note（不帶篩選＝全庫）。
    - limit：回傳 rows 的列數上限（預設 50，保護 context）；出圖與 report_data.json
      用各報表自己的預設列數，不受此限。
    - with_charts=False：只回數據、不落任何檔（快速問數字用）。
    - analysis_id：數據與圖表都改用該 analysis 的專利快照，圖檔登錄 export_runs。

    數據與圖表出自同一套報表定義與篩選條件，口徑一致；圖表檔案在回傳的
    charts.output_dir 下，index.html 是彙整頁。
    """
    selected_report_names = report_names or list(DEFAULT_REPORT_NAMES)
    unknown = sorted(set(selected_report_names) - set(REPORT_DEFINITIONS))
    if unknown:
        raise ValueError(f"未知報表名：{', '.join(unknown)}（用 list_reports 查可用報表）")

    # analysis 快照只查一次，數據與圖表共用同一組 patent_ids 口徑。
    patent_ids = fetch_analysis_patent_ids(analysis_id) if analysis_id is not None else None

    data = run_reports_batch(
        list(selected_report_names), filters=filters, limit=limit, patent_ids=patent_ids
    )
    result: dict[str, Any] = {
        "reports": data,
        "parameters": {
            "report_names": list(selected_report_names),
            "filters": filters or None,
            "row_limit": limit,
            "with_charts": with_charts,
            "analysis_id": analysis_id,
        },
    }

    if with_charts:
        chart_result = run_chart_trial(
            analysis_id=analysis_id,
            report_names=list(selected_report_names),
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
        if analysis_id is not None:
            # report_data.json 是前端與 AI 解讀共同使用的結構化數據；有 analysis/run
            # 脈絡時同步版本化回存 DB，避免只靠檔案路徑追溯。
            saved = save_workflow_output(
                int(analysis_id),
                "report_data",
                _load_report_data_json(chart_result["output_dir"]),
            )
            charts["report_data_version"] = saved["version"]
        result["charts"] = charts

    return json_safe(result)


# 敘述型 AI 專用道前綴：一般 workflow output 不得佔用（只准 save_analysis_narrative 走）。
_AI_OUTPUT_PREFIX = "ai:"
# derived 刷新範圍白名單：aliases＝含公司正規化名稱的 report_patent_base；
# report_views＝家族層 report view；all＝兩者（base 先於 family，維持相依順序）。
_REFRESH_SCOPES = ("aliases", "report_views", "all")


def save_workflow_output(run_id: int, output_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """版本化 append 一筆 workflow output（薄包 workflow_outputs_repository，不寫第二份邏輯）。

    guard：output_type 不得為空、不得以 'ai:' 起（敘述型專用道保留給 save_analysis_narrative）；
    payload 帶 artifact_key 者走 append_artifact_output（套引擎既有副檔白名單 .png/.svg/.jpg/.jpeg/
    .pptx），否則走 append_output 版本化 append（append-only，不覆蓋舊版）。回傳新版本號。
    """
    from backend.app.repositories.workflow_outputs_repository import (
        PostgresWorkflowOutputsRepository,
    )

    ot = str(output_type or "").strip()
    if not ot:
        raise ValueError("output_type 不得為空")
    if ot.startswith(_AI_OUTPUT_PREFIX):
        raise ValueError(
            f"output_type {_AI_OUTPUT_PREFIX!r} 前綴為敘述型專用道，請改用 save_analysis_narrative")
    if not isinstance(payload, dict):
        raise ValueError("payload 必須為 dict")

    repo = PostgresWorkflowOutputsRepository()
    if "artifact_key" in payload:
        version = repo.append_artifact_output(int(run_id), ot, payload)
    else:
        version = repo.append_output(int(run_id), ot, payload)
    return json_safe({"run_id": int(run_id), "output_type": ot, "version": version})


def refresh_derived_data(scope: str) -> dict[str, Any]:
    """刷新 derived 層（薄包既有 refresh 函式）。scope 白名單見 _REFRESH_SCOPES。

    - aliases：report_patent_base（applicant 正規化名稱落此表）。
    - report_views：report_family_country（家族×國家 report view）。
    - all：兩者依相依順序（base → family）。
    回傳每步影響列數（各 refresh 函式原樣 summary）與耗時 elapsed_ms。
    """
    import time

    from backend.app.derived.refresh_report_family_country import refresh_report_family_country
    from backend.app.derived.refresh_report_patent_base import refresh_report_patent_base

    name = str(scope or "").strip()
    if name not in _REFRESH_SCOPES:
        raise ValueError(f"unsupported scope: {scope!r}；限 {list(_REFRESH_SCOPES)}")
    plans = {
        "aliases": [("report_patent_base", refresh_report_patent_base)],
        "report_views": [("report_family_country", refresh_report_family_country)],
        "all": [("report_patent_base", refresh_report_patent_base),
                ("report_family_country", refresh_report_family_country)],
    }
    steps: list[dict[str, Any]] = []
    for step_name, fn in plans[name]:
        started = time.perf_counter()
        summary = fn()
        steps.append({
            "step": step_name,
            "summary": summary,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        })
    return json_safe({"scope": name, "steps": steps})


# ⚠ 2026-08-13 移除 generate_report_ppt（PPT 交付線已於 2026-08-10 整條退場）。
# 它建的是 report_generate job，payload 帶 {"version": v, "artifact": "ppt"}，但
# handle_report_generate **兩個鍵都不讀**——version 不消費（該 job 是產*新*版本，
# 不是對指定版本再加工）、artifact 全庫無消費者。於是呼叫它＝排一筆重產全部報表的
# job，卻回 run_id 與 "queued": true，呼叫端以為在產 PPT。
# 🔴 比報 422 更糟：**不報錯**，回報成功卻做了別的事。


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
