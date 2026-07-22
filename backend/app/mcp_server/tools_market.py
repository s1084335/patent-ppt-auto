"""Market evidence 的 MCP 工具層。

這裡只包裝 MarketStore、候選 evidence workflow 與彙總工具，不直接處理
Claude CLI 的瀏覽或搜尋。正式流程是：

1. prepare_market_evidence_task 產生外部研究任務。
2. save_market_evidence_candidates 暫存 Claude CLI 回傳的候選 evidence。
3. 使用者確認後，accept_market_evidence_candidates 才寫入正式 market_evidence。
"""
from __future__ import annotations

from typing import Any

from backend.app.market.evidence_workflow import (
    ACCEPTANCE_OUTPUT_TYPE,
    build_candidate_output_payload,
    build_market_research_task,
    select_accepted_candidates,
)
from backend.app.market.market_store import MarketStore
from backend.app.mcp_server.tools_reporting import save_workflow_output


def get_market_evidence(
    kind: str | None = None,
    scope: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """依 kind、scope、target 查詢已確認的正式 market evidence。"""
    rows = MarketStore().get_evidence(kind=kind, scope=scope, target=target)
    return {"count": len(rows), "evidence": rows}


def save_market_evidence(
    kind: str,
    scope: str,
    target: str | None,
    payload_json: dict[str, Any],
    source_url: str,
    summary: str,
) -> dict[str, Any]:
    """直接寫入一筆已確認 evidence；一般 Claude CLI 流程不應直接呼叫此工具。"""
    new_id = MarketStore().save_evidence(
        kind=kind,
        scope=scope,
        target=target,
        payload_json=payload_json,
        source_url=source_url,
        summary=summary,
    )
    return {"id": new_id, "accepted": True}


def aggregate_market_evidence(scope: str | None = None) -> dict[str, Any]:
    """彙總正式 market evidence，輸出 min/max、single_source、divergent 等報表資訊。"""
    return MarketStore().aggregate_for_report(scope=scope)


def prepare_market_evidence_task(
    scope: str,
    targets: list[str],
    kinds: list[str] | None = None,
    report_version: str | None = None,
) -> dict[str, Any]:
    """產生給 Claude CLI 的外部市場研究任務，不寫入正式資料表。"""
    return build_market_research_task(
        scope=scope,
        targets=targets,
        kinds=kinds,
        report_version=report_version,
    )


def save_market_evidence_candidates(
    run_id: int,
    scope: str,
    candidates: list[dict[str, Any]],
    report_version: str | None = None,
) -> dict[str, Any]:
    """把 Claude CLI 回傳的候選 evidence 暫存到 workflow_outputs。"""
    payload = build_candidate_output_payload(
        scope=scope,
        candidates=candidates,
        report_version=report_version,
    )
    return save_workflow_output(int(run_id), ACCEPTANCE_OUTPUT_TYPE, payload)


def accept_market_evidence_candidates(
    candidates: list[dict[str, Any]],
    accepted_indexes: list[int] | None = None,
) -> dict[str, Any]:
    """將使用者確認的候選 evidence 寫入正式 market_evidence。"""
    selected = select_accepted_candidates(candidates, accepted_indexes)
    store = MarketStore()
    ids: list[int] = []
    for candidate in selected:
        ids.append(
            store.save_evidence(
                candidate["kind"],
                candidate["scope"],
                candidate["target"],
                candidate["payload_json"],
                candidate["source_url"],
                candidate["summary"],
            )
        )
    return {"accepted_count": len(ids), "ids": ids}
