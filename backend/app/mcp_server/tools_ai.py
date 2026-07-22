"""Patent MCP Server — AI 任務工具（純函式層，不 import mcp SDK）。

補齊「AI 任務自動化」E2E 缺的兩支：
- get_report_payload：定案 #9「數據為主、圖表為輔」的取數口——回單一報表完整 rows＋圖檔 artifact key。
- save_analysis_narrative：敘述型 AI 落點（2026-07-17 定案＝通用回存）——走 workflow_outputs
  版本化 append，output_type 強制 'ai:' 前綴（敘述型專用道 guard），欄位承載 narratives 契約
  （text/ai_model/prompt_version/based_on_version/generated_at，見 report-narrative-flow.md）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.mcp_server._shared import json_safe
from backend.app.repositories.workflow_outputs_repository import (
    PostgresWorkflowOutputsRepository,
)

# 敘述型 AI 回存專用道前綴（guard）：只准走 'ai:' namespace，不與結構型 domain 落點混淆。
AI_OUTPUT_PREFIX = "ai:"


class NarrativeGuardError(ValueError):
    """敘述型 AI 落點護欄違規（output_type 非 'ai:' 專用道、section 非法或內容空）。"""


def require_ai_prefix(output_type: str) -> str:
    """守門：敘述型回存的 output_type 必須以 'ai:' 起，否則拒絕。"""
    if not isinstance(output_type, str) or not output_type.startswith(AI_OUTPUT_PREFIX):
        raise NarrativeGuardError(
            f"敘述型回存 output_type 必須以 {AI_OUTPUT_PREFIX!r} 起：{output_type!r}")
    return output_type


def _narrative_output_type(section: str) -> str:
    """由 section 組出 'ai:narrative:<section>' output_type（section 不得空、不得自帶 ai: 前綴）。"""
    name = str(section or "").strip()
    if not name:
        raise NarrativeGuardError("section 不得為空")
    if name.startswith(AI_OUTPUT_PREFIX):
        raise NarrativeGuardError(f"section 不得自帶 {AI_OUTPUT_PREFIX!r} 前綴：{section!r}")
    return require_ai_prefix(f"{AI_OUTPUT_PREFIX}narrative:{name}")


def save_analysis_narrative(
    run_id: int,
    section: str,
    content: str,
    ai_model: str,
    prompt_version: str,
    based_on_version: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """敘述型 AI 回存：走 PostgresWorkflowOutputsRepository 版本化 append，不覆蓋舊版。

    output_type 固定 'ai:narrative:<section>'（敘述型專用道 guard）；data_json 承載 narratives
    契約（text/ai_model/prompt_version/based_on_version/generated_at）。回傳新版本號。
    """
    if not str(content or "").strip():
        raise NarrativeGuardError("content 不得為空")
    if not str(ai_model or "").strip() or not str(prompt_version or "").strip():
        raise NarrativeGuardError("ai_model 與 prompt_version 必填")
    output_type = _narrative_output_type(section)
    data_json = {
        "section": section,
        "text": content,
        "ai_model": ai_model,
        "prompt_version": prompt_version,
        "based_on_version": based_on_version,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
    }
    version = PostgresWorkflowOutputsRepository().append_output(int(run_id), output_type, data_json)
    return json_safe({
        "run_id": int(run_id),
        "output_type": output_type,
        "version": version,
        "section": section,
    })


def get_report_payload(report_name: str) -> dict[str, Any]:
    """回單一報表完整 rows＋對應圖檔 artifact key（供 Claude 解讀：數據為主、圖表為輔）。

    自取設計（回報列出）：
    - 資料來源＝report_engine 即時重查（run_reports_batch，limit=None＝先取全量再裁），與出圖同一口徑；
      不綁定某次 report_trial 落檔，避免 latest run 選擇歧義。
    - rows 一律裁前 PERSIST_RANKING_ROWS（＝引擎入庫排名列數上限，不另設新值），保護 LLM context；
      裁前總數收進 rows_total（data 內與頂層各一份），呼叫端據此得知是否被裁與完整筆數。
    - artifact key＝'<report_name>.svg' 慣例（實際圖檔於 run_report_analysis(with_charts) 產於
      charts.output_dir，key 供 client 對應圖與數據）。
    """
    from backend.app.reports.chart_runner import PERSIST_RANKING_ROWS
    from backend.app.reports.report_definitions import REPORT_DEFINITIONS
    from backend.app.reports.report_engine import run_reports_batch

    if report_name not in REPORT_DEFINITIONS:
        raise ValueError(f"未知報表名：{report_name}（用 list_reports 查可用報表）")
    data = run_reports_batch([report_name], filters=None, limit=None)
    report = data.get(report_name)
    rows = report.get("rows") if isinstance(report, dict) else report
    rows_total = len(rows) if isinstance(rows, list) else None
    # 一律裁前 20（沿用引擎 PERSIST_RANKING_ROWS）；rows_total 標記裁前總數。
    if isinstance(report, dict) and isinstance(rows, list):
        report = {**report, "rows": rows[:PERSIST_RANKING_ROWS], "rows_total": rows_total}
        kept_rows = report["rows"]
    else:
        kept_rows = rows
    return json_safe({
        "report_name": report_name,
        "data": report,
        "row_count": len(kept_rows) if isinstance(kept_rows, list) else None,
        "rows_total": rows_total,
        "artifact_keys": [f"{report_name}.svg"],
    })
