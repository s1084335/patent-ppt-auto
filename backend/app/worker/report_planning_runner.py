"""goal-driven 報告規劃 runner（P2 第 4 節）。

把「最大目標＋使用者選定的圖表＋唯讀查證工具」交給 headless CLI，取回
`ReportStrategy`／`SlidePlan`／`EvidenceManifest`，驗證後回傳候選。

🔴 分工（design.md 第 4 點）：
- CLI **只產結構化候選**——沒有任何 DB／artifact 寫入工具；要補證據一律經
  report-research 唯讀 MCP。
- runner 是**唯一保存者**：驗證未過就不落任何 artifact（失敗的規劃不得留痕
  被誤當成可交付）。
- 形狀規則一律走 `planning_contracts`（唯一定義處），本模組不另寫一套。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from backend.app.mcp_server.report_research import TOOL_NAMES
from backend.app.reports.planning_contracts import (
    APPROVED_LAYOUT_PRESETS,
    validate_evidence,
    validate_report_brief,
    validate_slide_plan,
)

PROMPT_VERSION = "report_planning_v1"
DEFAULT_CLI_TIMEOUT_SECONDS = 900.0


class ReportPlanningError(RuntimeError):
    """規劃失敗（brief 不合格、CLI 產出不合契約、驗證未過）。"""


def build_prompt(brief: dict[str, Any]) -> str:
    """組規劃提示：目標／受眾／頁數預算＋每張選圖的圖與數據＋可用查證工具。"""
    charts = brief.get("selected_charts") or []
    chart_blocks = []
    for bundle in charts:
        rows = json.dumps(bundle.get("data_rows") or [], ensure_ascii=False)[:1500]
        chart_blocks.append(
            f"### {bundle['chart_identity']}｜{bundle.get('title') or ''}\n"
            f"- 圖檔：{bundle.get('image_path')}\n"
            f"- 母體：{bundle.get('population_note') or '（未標）'}\n"
            f"- 數據列：{rows}"
        )
    tools = "、".join(TOOL_NAMES)
    presets = "、".join(sorted(APPROVED_LAYOUT_PRESETS))
    return (
        "任務：依最大目標規劃一份專利分析簡報（系統派工、非互動、一次性）。\n\n"
        f"## 最大目標\n{brief['north_star_goal']}\n"
        + ("（使用者未指定目標，以下為系統預設策略；品質標準不因此降低）\n\n"
           if brief.get("used_default_goal") else "\n")
        + f"## 受眾\n{brief.get('audience') or '未指定'}\n\n"
        + "## 編排方向（方向不是模板；版型是備選庫，出哪幾頁由內容決定）\n"
        + "".join(f"- {d}\n" for d in (brief.get("directions") or [])) + "\n"
        + "## 品質標準（兩份參考範例的共同 DNA）\n"
          "- 結論先行：開頭就要有可行動的判斷，不是把結論留到最後\n"
          "- 每頁要有**具名發現**與依據（誰、哪個主題、幾件），不是泛稱\n"
          "- Key Player 要有定位（全領域／單一技術深布局／利基／前案），不只排名\n"
          "- 收尾要有判讀說明：母體口徑、可觀測性偏差、資料限制\n"
          "- **每頁要點 2–4 條、每條 30 字內**：版面放不下的要點會被整條丟棄，\n"
          "  寫得長不等於講得多；把話說準比說滿重要\n\n"
        f"## 頁數上限\n{brief['page_budget']} 頁（超過即不合格）\n\n"
        "## 使用者選定的圖表（**全部都要用到，且不得加入未選的圖**）\n"
        + "\n\n".join(chart_blocks) + "\n\n"
        "## 可用的唯讀查證工具（要補證據就呼叫，不得自行編數字）\n"
        f"{tools}\n"
        f"（快照型查詢一律帶 snapshot_id=\"{brief['snapshot_id']}\"，收 typed 參數不吃 SQL；\n"
        " `query_database` 是唯一連資料庫的工具，收單句 SELECT／WITH——選圖數據\n"
        " 答不出來的問題才用它，並在 evidence 標 source=\"tool_query\"）\n\n"
        "## 可選版型（備選庫，不是必出清單——**依內容決定出哪幾種**）\n"
        f"{presets}\n\n"
        "## 輸出（只輸出這個 JSON）\n"
        '{"strategy": {"north_star_goal": "...", "storyline": ["..."]},\n'
        ' "slides": [{"slide_id": "s1", "layout_preset": "<上列其一>",\n'
        '   "purpose": "這頁要回答什麼", "chart_identities": ["..."],\n'
        '   "narrative": [{"text": "...", "evidence_ref": "e1"}]}],\n'
        ' "evidence": {"e1": {"source": "selected_chart|tool_query",\n'
        '   "chart_identity": "...", "snapshot_id": "..."}}}\n\n'
        "規則：\n"
        "- **帶數字的敘述一律要有 evidence_ref**，數字只能來自選圖數據或查證工具。\n"
        "- 只給版型意圖，**不要輸出座標、字級、顏色**——排版由程式決定。\n"
        "- 每一張選圖至少要出現在一頁；沒有內容支撐的版型就不要用。\n"
    )


def _parse_reply(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ReportPlanningError(f"CLI 回覆非 JSON：{text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ReportPlanningError(f"CLI JSON 解析失敗：{exc}") from exc


def run_report_planning(
    brief: dict[str, Any],
    cli_runner: Callable[..., str],
    persister: Callable[[dict[str, Any]], Any] | None = None,
    timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """brief → CLI → 驗證 → 候選 plan。驗證未過一律 raise，不落 artifact。"""
    def _tick(stage: str, pct: int) -> None:
        if progress:
            progress(stage, pct)

    brief_errors = validate_report_brief(brief)
    if brief_errors:
        raise ReportPlanningError(f"ReportBrief 不合格：{brief_errors}")

    _tick("CLI 規劃中", 30)
    reply = _parse_reply(cli_runner(build_prompt(brief), timeout_seconds=timeout_seconds))

    plan = {
        "plan_id": reply.get("plan_id") or f"plan-{brief['snapshot_id']}",
        "slides": reply.get("slides") or [],
    }
    evidence = reply.get("evidence") or {}
    identities = {b["chart_identity"] for b in brief["selected_charts"]}

    _tick("驗證規劃", 70)
    errors = validate_slide_plan(plan, identities, page_budget=brief.get("page_budget"))
    errors += validate_evidence(plan, evidence, snapshot_id=brief["snapshot_id"])
    if errors:
        # ⚠ 失敗不落檔：留下未通過的候選會讓人誤以為可交付。
        raise ReportPlanningError(f"規劃驗證未過：{errors}")

    result = {
        "plan_id": plan["plan_id"],
        "plan": plan,
        "slides": plan["slides"],
        "strategy": reply.get("strategy") or {},
        "evidence": evidence,
        "prompt_version": PROMPT_VERSION,
        "snapshot_id": brief["snapshot_id"],
        "validation_errors": [],
    }
    _tick("保存候選", 90)
    if persister is not None:
        persister(result)
    return result
