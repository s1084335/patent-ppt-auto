"""局部重產 runner（openspec `enable-goal-driven-readonly-report-planning` 6.4）。

把品質 gate 與 scope lock 接起來：

1. `build_ppt_quality_report`（`planning_contracts`）判定 `regenerate_partial`
   並產出 `RegenerationPlan`——哪幾頁要重產、哪些不准動。
2. 本 runner 只把 **targets 指名的頁**交給 CLI 重產。
3. `validate_regeneration_response` 驗越界；任一項不過就整份拒收。
4. 合併：只換 target 頁，其餘**原樣保留**，頁序不動。
5. 留下 replacement audit：哪一頁、為什麼、第幾次。

🔴 為什麼合併不能整份覆蓋：局部重產的前提是「其餘內容已驗收過」。整份覆蓋等於
讓沒被指定的頁跳過驗收又進成品——使用者以為只動了一頁，實際上整份都換了。
這與本專案反覆出現的靜默退化同型：失敗時看起來像正常行為。

⚠ 不含轉 PNG 與重跑 quality report——那屬驗收線（A5）。本模組刻意只做
「換頁並留下可追溯的紀錄」，因為這樣才能獨立驗收：給定 plan 與 CLI 回應，
輸出的 slide_plan 必須只有 targets 那幾頁變了。

⚠ 規則一律走 `planning_contracts`（唯一定義處）：重試上限、scope lock 判準
都不在本模組重寫。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.app.reports.planning_contracts import (
    PPT_QUALITY_RETRY_LIMIT,
    validate_regeneration_response,
)


class RegenerationError(RuntimeError):
    """重產不合契約（decision 不對、target 不存在、CLI 越界、超過重試上限）。"""


def _target_ids(plan: dict[str, Any]) -> list[str]:
    return [str(t.get("slide_id") or "") for t in (plan.get("targets") or [])
            if str(t.get("slide_id") or "").strip()]


def _reason_of(plan: dict[str, Any], slide_id: str) -> str:
    for target in plan.get("targets") or []:
        if str(target.get("slide_id") or "") == slide_id:
            return str(target.get("reason") or "")
    return ""


def run_partial_regeneration(
    plan: dict[str, Any],
    slides: list[dict[str, Any]],
    cli_runner: Callable[[dict[str, Any]], dict[str, Any]],
    attempt: int = 1,
) -> dict[str, Any]:
    """依 `RegenerationPlan` 重產指定頁，回傳合併後的 slides 與 replacement audit。

    `cli_runner` 收一份 payload（plan ＋ 要重產的原頁內容），回傳
    `{"slides": [...]}`。注入點供測試餵 fake，正式路徑由 ai_bridge 接真 CLI。
    """
    if str(plan.get("decision") or "") != "regenerate_partial":
        raise RegenerationError(
            f"decision {plan.get('decision')!r} 不是 regenerate_partial——"
            "整份重產與 blocked 各有各的路徑，不共用本 runner")

    if attempt > PPT_QUALITY_RETRY_LIMIT:
        raise RegenerationError(
            f"同一目標已重產 {attempt} 次（上限 {PPT_QUALITY_RETRY_LIMIT}）——"
            "停止自動重產並標記 blocked，改由人工判定根因")

    targets = _target_ids(plan)
    if not targets:
        raise RegenerationError("RegenerationPlan 沒有 targets——沒有指名要重產哪一頁")

    by_id = {str(s.get("slide_id") or ""): s for s in slides}
    missing = [sid for sid in targets if sid not in by_id]
    if missing:
        raise RegenerationError(
            f"target 指向原 plan 沒有的頁 {missing}——不得靜默新增頁面")

    payload = {
        "plan": plan,
        "targets": [{"slide_id": sid, "reason": _reason_of(plan, sid),
                     "current": by_id[sid]} for sid in targets],
    }
    response = cli_runner(payload) or {}

    errors = validate_regeneration_response(plan, response, attempt=attempt)
    if errors:
        # ⚠ 整份拒收，不得部分採用——部分採用等於讓越界的那一頁進成品。
        raise RegenerationError(f"重產回應越界：{errors}")

    replacements = {str(s.get("slide_id") or ""): s for s in response.get("slides") or []}
    merged = [replacements.get(str(s.get("slide_id") or ""), s) for s in slides]
    audit = [
        {"slide_id": sid, "reason": _reason_of(plan, sid), "attempt": attempt}
        for sid in targets if sid in replacements
    ]
    return {"slides": merged, "replacement_audit": audit}
