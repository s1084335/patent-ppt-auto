"""Market evidence 候選與人工確認流程。

Claude CLI 可以協助搜尋外部市場資料，但它產出的內容只能先成為候選
evidence。候選資料會存到 workflow_outputs，必須經使用者確認後，才可寫入
derived_layer.market_evidence，避免把未驗證的 AI 研究結果直接變成正式報表依據。
"""
from __future__ import annotations

from typing import Any

from backend.app.market.evidence_model import KINDS, MarketEvidenceError, validate_evidence

ACCEPTANCE_OUTPUT_TYPE = "market:evidence_candidates"
_PAYLOAD_TRACE_FIELDS = ("evidence_excerpt",)


def build_market_research_task(
    scope: str,
    targets: list[str],
    kinds: list[str] | None = None,
    report_version: str | None = None,
) -> dict[str, Any]:
    """建立交給 Claude CLI 的外部市場研究任務 brief。

    brief 只描述研究範圍、輸出 schema 與防幻覺規則；Claude CLI 不應直接呼叫
    正式寫入工具。正式入庫要走候選暫存與使用者確認兩段式流程。
    """
    clean_scope = str(scope or "").strip()
    clean_targets = [str(target).strip() for target in targets if str(target or "").strip()]
    selected_kinds = list(kinds or KINDS)
    _validate_task_inputs(clean_scope, clean_targets, selected_kinds)

    return {
        "status": "needs_external_research",
        "scope": clean_scope,
        "targets": clean_targets,
        "kinds": selected_kinds,
        "report_version": report_version,
        "output_type": ACCEPTANCE_OUTPUT_TYPE,
        "candidate_schema": {
            "required": ["kind", "scope", "target", "payload_json", "source_url", "summary"],
            "payload_json_required": [
                "source_name",
                "source_url",
                "published_on",
                "reliability",
                "summary",
                "evidence_excerpt",
            ],
        },
        "anti_hallucination_rules": [
            "每一筆候選 evidence 必須提供可公開查證的 source_url。",
            "payload_json.source_url 必須和外層 source_url 完全一致。",
            "payload_json.evidence_excerpt 必須包含來源中可核對的短摘錄，不可只寫 AI 摘要。",
            "找不到可靠來源時，回報 needs_more_research，不要臆測數字或結論。",
            "市場規模、市占、CAGR、痛點或區域趨勢不得由專利件數自行推估。",
        ],
        "acceptance_rules": [
            "Claude CLI 不得直接呼叫 save_market_evidence。",
            "Claude CLI 產出的候選 evidence 必須先用 save_market_evidence_candidates 存入 workflow_outputs。",
            "只有使用者確認後，系統才可呼叫 accept_market_evidence_candidates 寫入正式 market_evidence。",
        ],
    }


def validate_candidate(candidate: dict[str, Any]) -> None:
    """驗證候選 evidence 是否足夠可追溯、可交給使用者確認。"""
    normalized = normalize_candidate(candidate)
    payload = normalized["payload_json"]
    payload_url = str(payload.get("source_url") or "").strip()
    source_url = str(normalized["source_url"] or "").strip()
    if payload_url != source_url:
        raise MarketEvidenceError("payload_json.source_url 必須和 source_url 一致")
    for field in _PAYLOAD_TRACE_FIELDS:
        if not str(payload.get(field) or "").strip():
            raise MarketEvidenceError(f"payload_json 缺少可追溯欄位：{field}")
    validate_evidence(_to_validation_shape(normalized))


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """把 Claude CLI 輸出的候選 evidence 整理成 MarketStore 可用欄位。"""
    if not isinstance(candidate, dict):
        raise MarketEvidenceError("candidate 必須是 dict")
    payload = candidate.get("payload_json")
    if not isinstance(payload, dict):
        raise MarketEvidenceError("candidate 缺少 payload_json")
    normalized = {
        "kind": str(candidate.get("kind") or "").strip(),
        "scope": str(candidate.get("scope") or "").strip(),
        "target": candidate.get("target"),
        "payload_json": dict(payload),
        "source_url": str(candidate.get("source_url") or "").strip(),
        "summary": str(candidate.get("summary") or "").strip(),
    }
    if normalized["target"] is not None:
        normalized["target"] = str(normalized["target"]).strip()
    return normalized


def build_candidate_output_payload(
    scope: str,
    candidates: list[dict[str, Any]],
    report_version: str | None = None,
) -> dict[str, Any]:
    """建立要暫存到 workflow_outputs 的候選 evidence payload。"""
    normalized_candidates = []
    for candidate in candidates:
        validate_candidate(candidate)
        normalized_candidates.append(normalize_candidate(candidate))
    return {
        "scope": str(scope or "").strip(),
        "report_version": report_version,
        "candidate_count": len(normalized_candidates),
        "candidates": normalized_candidates,
        "guard": {
            "accepted": False,
            "reason": "候選 evidence 尚未經使用者確認，不得視為正式市場資料。",
        },
    }


def select_accepted_candidates(
    candidates: list[dict[str, Any]],
    accepted_indexes: list[int] | None = None,
) -> list[dict[str, Any]]:
    """依使用者確認的 index 選出可寫入正式表的候選 evidence。"""
    if accepted_indexes is None:
        selected = list(candidates)
    else:
        selected = []
        for index in accepted_indexes:
            if index < 0 or index >= len(candidates):
                raise MarketEvidenceError(f"accepted index out of range: {index}")
            selected.append(candidates[index])
    normalized_selected = []
    for candidate in selected:
        validate_candidate(candidate)
        normalized_selected.append(normalize_candidate(candidate))
    return normalized_selected


def _validate_task_inputs(scope: str, targets: list[str], kinds: list[str]) -> None:
    """檢查研究任務輸入，避免送出空範圍或未知 evidence kind。"""
    if not scope:
        raise MarketEvidenceError("scope 不可為空")
    if not targets:
        raise MarketEvidenceError("targets 不可為空")
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown:
        raise MarketEvidenceError(f"unsupported market evidence kind: {unknown}")


def _to_validation_shape(candidate: dict[str, Any]) -> dict[str, Any]:
    """轉成 evidence_model.validate_evidence 既有驗證函式需要的形狀。"""
    evidence = {
        "kind": candidate["kind"],
        "scope": candidate["scope"],
        "payload_json": candidate["payload_json"],
    }
    if candidate["kind"] in {"market_size", "region_trend", "key_player"}:
        evidence["market"] = candidate["target"]
    if candidate["kind"] in {"customer", "pain_point"}:
        evidence["subject"] = candidate["target"]
    return evidence
