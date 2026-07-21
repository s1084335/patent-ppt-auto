"""案件比對 · 要素四態、all-elements rule 與引用鏈推論（純邏輯，無 DB）。

依 2026-07-20 定案：
- 要素四態：met / arguably_met / not_met / insufficient_info（其他值拒絕）。
- all-elements rule（程式執行，非 LLM 判定）：任一必要要素 not_met → 該 Claim not_established；
  全部 met → possibly_established；其餘（含 arguably_met／insufficient_info）→ needs_review。
- 第一版所有 element 皆視為必要（all-elements rule 本義：每個 claim 要素都須具備）。
- 引用鏈推論：獨立項 not_established → 其分支從屬項推論 not_established（inferred=True，不看自身要素）；
  獨立項 possibly_established 或 needs_review → 從屬項只比對新增限制，並與父項取較弱者。

侵權與否的法律結論一律由人工做成；本模組只做規則彙總，不輸出法律判定。
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class VerdictError(ValueError):
    """四態值非法、claim 結構非法或引用鏈異常。"""


class ElementStatus(str, Enum):
    """要素四態。"""

    MET = "met"
    ARGUABLY_MET = "arguably_met"
    NOT_MET = "not_met"
    INSUFFICIENT_INFO = "insufficient_info"


class ClaimStatus(str, Enum):
    """Claim 級彙總狀態（初判，非法律結論）。"""

    NOT_ESTABLISHED = "not_established"
    POSSIBLY_ESTABLISHED = "possibly_established"
    NEEDS_REVIEW = "needs_review"


# severity：not_established 最弱(0) < needs_review(1) < possibly_established(2)；
# 從屬項結合父項時取較弱者（min severity），確保父項不利即拉低整體。
_SEVERITY = {
    ClaimStatus.NOT_ESTABLISHED: 0,
    ClaimStatus.NEEDS_REVIEW: 1,
    ClaimStatus.POSSIBLY_ESTABLISHED: 2,
}


def parse_element_status(value: Any) -> ElementStatus:
    """把外部值轉為 ElementStatus；非四態即拋 VerdictError。"""
    try:
        return ElementStatus(value)
    except ValueError as exc:
        raise VerdictError(f"非法要素四態：{value!r}") from exc


def parse_claim_status(value: Any) -> ClaimStatus:
    """把外部值轉為 ClaimStatus；非法即拋 VerdictError（供寫入層縱深防禦）。"""
    try:
        return ClaimStatus(value)
    except ValueError as exc:
        raise VerdictError(f"非法 claim 狀態：{value!r}") from exc


def evaluate_claim(element_statuses: list[Any]) -> ClaimStatus:
    """對單一 claim 的要素四態套 all-elements rule，回傳 ClaimStatus。"""
    statuses = [parse_element_status(s) for s in element_statuses]
    if not statuses:
        raise VerdictError("claim 至少需一個要素才能套用 all-elements rule")
    if any(s == ElementStatus.NOT_MET for s in statuses):
        return ClaimStatus.NOT_ESTABLISHED
    if all(s == ElementStatus.MET for s in statuses):
        return ClaimStatus.POSSIBLY_ESTABLISHED
    return ClaimStatus.NEEDS_REVIEW


def _combine(parent: ClaimStatus, own: ClaimStatus) -> ClaimStatus:
    """從屬項與父項結合：取 severity 較低（較不利）者。"""
    return parent if _SEVERITY[parent] <= _SEVERITY[own] else own


def infer_verdicts(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """依引用鏈推論每個 claim 的彙總狀態。

    輸入每項：{claim_number, type: 'independent'|'dependent', parent?（從屬項必填）,
    element_statuses（獨立項＝全要素；從屬項＝新增限制）}。
    回傳 {claim_number: {'status': ClaimStatus, 'inferred': bool}}。
    inferred=True 表示未實比對自身要素、由父項 not_established 推論而得。
    """
    by_num: dict[str, dict[str, Any]] = {}
    for claim in claims:
        num = str(claim.get("claim_number", "")).strip()
        if not num:
            raise VerdictError("claim 缺少 claim_number")
        if num in by_num:
            raise VerdictError(f"claim_number 重複：{num}")
        by_num[num] = claim

    results: dict[str, dict[str, Any]] = {}

    def resolve(num: str, stack: frozenset) -> dict[str, Any]:
        if num in results:
            return results[num]
        if num in stack:
            raise VerdictError(f"引用鏈存在環：{num}")
        claim = by_num.get(num)
        if claim is None:
            raise VerdictError(f"claim {num} 不存在")
        ctype = claim.get("type")
        if ctype == "independent":
            res = {"status": evaluate_claim(claim.get("element_statuses", [])), "inferred": False}
        elif ctype == "dependent":
            parent = str(claim.get("parent", "")).strip()
            if not parent:
                raise VerdictError(f"從屬項 {num} 缺少 parent")
            parent_res = resolve(parent, stack | {num})
            if parent_res["status"] == ClaimStatus.NOT_ESTABLISHED:
                # 父項不成立 → 分支從屬項推論不成立，不看自身要素
                res = {"status": ClaimStatus.NOT_ESTABLISHED, "inferred": True}
            else:
                own = evaluate_claim(claim.get("element_statuses", []))
                res = {"status": _combine(parent_res["status"], own), "inferred": False}
        else:
            raise VerdictError(f"claim {num} 的 type 非法：{ctype!r}")
        results[num] = res
        return res

    for num in by_num:
        resolve(num, frozenset())
    return results
