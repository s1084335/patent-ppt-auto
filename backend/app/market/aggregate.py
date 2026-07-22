"""市場資料 · 確定性彙總與 AI 第一篩輔助（純邏輯，無 DB／網路）。

彙總鐵律：同可比較性 key 才併算 min–max（不平均）；單一來源標 single_source、
差異過大標 divergent；每個彙總值附 evidence 引用清單。篩選本身是 AI 的事，
本模組只提供確定性工具（dedup／時效排序／可靠性分級排序）。
"""
from __future__ import annotations

from typing import Any

from backend.app.market.evidence_model import _parse_date, comparability_key

# divergent 閾值（自取設計）：同 key 內相對全距 (max-min)/min > 0.5（即 >50% 口徑落差）標 divergent
DIVERGENT_REL_SPREAD = 0.5

# 可靠性分級：industry_gov_corp > news > forum（數字小＝優先）
RELIABILITY_RANK = {"industry_gov_corp": 0, "news": 1, "forum": 2}


def _value_of(evidence: dict[str, Any], metric: str) -> Any:
    return ((evidence.get("payload_json") or {}).get("value") or {}).get(metric)


def _source_name(evidence: dict[str, Any]) -> Any:
    return (evidence.get("payload_json") or {}).get("source_name")


def aggregate_metric(evidences: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    """對某數值 metric（規模／CAGR／預測／占比）按可比較性 key 併算 min–max。

    回傳每個 key 一筆：{comparability_key, metric, min, max, single_source, divergent, evidence[]}。
    不平均；不同 key 不混算；缺該 metric 的證據略過。
    """
    groups: dict[tuple, list[tuple[dict[str, Any], float]]] = {}
    for e in evidences:
        raw = _value_of(e, metric)
        if raw is None:
            continue
        groups.setdefault(comparability_key(e), []).append((e, float(raw)))

    result = []
    for key, items in groups.items():
        vals = [v for _, v in items]
        lo, hi = min(vals), max(vals)
        single = len(items) == 1
        divergent = (not single) and lo > 0 and (hi - lo) / lo > DIVERGENT_REL_SPREAD
        result.append({
            "comparability_key": key,
            "metric": metric,
            "min": lo,
            "max": hi,
            "single_source": single,
            "divergent": divergent,
            "evidence": [{"id": e.get("id"), "source_name": _source_name(e)} for e, _ in items],
        })
    return result


def dedup_key(evidence: dict[str, Any], metric: str) -> tuple:
    """去重鍵＝同 URL＋同 metric（AI 第一篩去重的確定性依據）。"""
    return ((evidence.get("payload_json") or {}).get("source_url"), metric)


def dedup(evidences: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    """依 (source_url, metric) 去重，保留先出現者。"""
    seen: set[tuple] = set()
    out = []
    for e in evidences:
        key = dedup_key(e, metric)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def sort_by_recency(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """依 published_on 由新到舊排序（時效優先）。"""
    return sorted(
        evidences,
        key=lambda e: _parse_date((e.get("payload_json") or {}).get("published_on")),
        reverse=True,
    )


def sort_by_reliability(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """依可靠性分級排序（industry_gov_corp→news→forum）。"""
    return sorted(
        evidences,
        key=lambda e: RELIABILITY_RANK.get((e.get("payload_json") or {}).get("reliability"), 99),
    )


def aggregate_region_trends(evidences: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    """區域趨勢結構化彙總：按 market 收整各來源的 trend 敘述與引用。"""
    out: dict[Any, list[dict[str, Any]]] = {}
    for e in evidences:
        if e.get("kind") != "region_trend":
            continue
        value = (e.get("payload_json") or {}).get("value") or {}
        out.setdefault(e.get("market"), []).append(
            {"trend": value.get("trend"), "source_name": _source_name(e), "id": e.get("id")})
    return out


def aggregate_customers(evidences: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    """銷售對象結構化彙總：按 subject（客群名）收整占比與引用。"""
    out: dict[Any, list[dict[str, Any]]] = {}
    for e in evidences:
        if e.get("kind") != "customer":
            continue
        value = (e.get("payload_json") or {}).get("value") or {}
        out.setdefault(e.get("subject"), []).append(
            {"share": value.get("share"), "source_name": _source_name(e), "id": e.get("id")})
    return out
