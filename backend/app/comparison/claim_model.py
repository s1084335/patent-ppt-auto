"""案件比對 · 專利理解稿（understanding draft）結構與來源欄位驗證（純邏輯，無 DB）。

驗證項目（依 2026-07-20「案件比對兩階段人工閘門」定案）：
- 獨立項清單非空：有幾項分析幾項，不得只有第一項（degenerate 空清單直接拒絕）。
- 每項要素分解：elements 非空、每要素含原文 text 與解釋 explanation 欄。
- 從屬項引用鏈：parent 必須存在（指向某獨立項或從屬項）、不得自引、無環。
- 關鍵 Claim 用語表：key_terms 須為 list（可空），每項含 term 與 definition。
- 來源限制欄位化：文字只能來自權利要求欄位（白名單），不得使用主權項或說明書。

結構非法時拋 ClaimModelError，訊息標明哪一項哪個欄位。
"""
from __future__ import annotations

from typing import Any

# 來源欄位白名單：優先「所有權利要求」，後備「獨立項＋從屬項」；
# 明確排除舊「主權項」與「說明書」（Claim 理解只讀權利要求欄位）。
ALLOWED_SOURCE_FIELDS = ("所有權利要求", "獨立項", "從屬項")


class ClaimModelError(ValueError):
    """理解稿結構或來源限制違規；訊息標明哪一項哪個欄位。"""


def validate_understanding(draft: dict[str, Any]) -> None:
    """驗證整份理解稿；非法即拋 ClaimModelError，合法回 None。"""
    if not isinstance(draft, dict):
        raise ClaimModelError("understanding 必須為 dict")
    _validate_source_fields(draft)

    independents = draft.get("independent_claims")
    if not isinstance(independents, list) or len(independents) == 0:
        raise ClaimModelError("independent_claims 不得為空：至少需分析一項獨立項")
    numbers: set[str] = set()
    for i, claim in enumerate(independents):
        num = _validate_claim_struct(claim, kind="independent", idx=i)
        if num in numbers:
            raise ClaimModelError(f"獨立項 claim_number 重複：{num}")
        numbers.add(num)

    dependents = draft.get("dependent_claims", [])
    if not isinstance(dependents, list):
        raise ClaimModelError("dependent_claims 必須為 list")
    for i, claim in enumerate(dependents):
        num = _validate_claim_struct(claim, kind="dependent", idx=i)
        if num in numbers:
            raise ClaimModelError(f"從屬項 claim_number 與其他 claim 重複：{num}")
        numbers.add(num)

    _validate_dependency_chain(independents, dependents)
    _validate_key_terms(draft)


def _validate_source_fields(draft: dict[str, Any]) -> None:
    """來源欄位必須非空且全在白名單內。"""
    source_fields = draft.get("source_fields")
    if not isinstance(source_fields, list) or len(source_fields) == 0:
        raise ClaimModelError("source_fields 不得為空：理解稿須標明文字來源欄位")
    for field in source_fields:
        if field not in ALLOWED_SOURCE_FIELDS:
            raise ClaimModelError(
                f"source_fields 含非權利要求欄位：{field!r}（僅允許 {ALLOWED_SOURCE_FIELDS}）")


def _validate_claim_struct(claim: Any, kind: str, idx: int) -> str:
    """驗證單一 claim 結構，回傳去空白後的 claim_number。"""
    where = f"{kind} claims[{idx}]"
    if not isinstance(claim, dict):
        raise ClaimModelError(f"{where} 必須為 dict")
    raw_num = claim.get("claim_number")
    if not isinstance(raw_num, str) or not raw_num.strip():
        raise ClaimModelError(f"{where} 缺少有效 claim_number")
    num = raw_num.strip()

    elements = claim.get("elements")
    if not isinstance(elements, list) or len(elements) == 0:
        raise ClaimModelError(f"claim {num} 的 elements 不得為空")
    for j, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ClaimModelError(f"claim {num} elements[{j}] 必須為 dict")
        text = element.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ClaimModelError(f"claim {num} elements[{j}] 缺少原文 text 欄")
        explanation = element.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ClaimModelError(f"claim {num} elements[{j}] 缺少解釋 explanation 欄")

    if kind == "dependent":
        parent = claim.get("parent")
        if not isinstance(parent, str) or not parent.strip():
            raise ClaimModelError(f"從屬項 claim {num} 缺少 parent 引用")
    return num


def _validate_dependency_chain(independents: list, dependents: list) -> None:
    """從屬項 parent 必須存在、不得自引、整體引用鏈無環。"""
    all_numbers = {c["claim_number"].strip() for c in independents}
    all_numbers |= {c["claim_number"].strip() for c in dependents}
    parents: dict[str, str] = {}
    for claim in dependents:
        num = claim["claim_number"].strip()
        parent = claim["parent"].strip()
        if parent == num:
            raise ClaimModelError(f"從屬項 claim {num} 不得引用自身")
        if parent not in all_numbers:
            raise ClaimModelError(f"從屬項 claim {num} 的 parent {parent} 不存在")
        parents[num] = parent

    # 沿 parent 追溯，途中重複造訪即為環
    for start in parents:
        seen: set[str] = set()
        cur = start
        while cur in parents:
            if cur in seen:
                raise ClaimModelError(f"從屬項引用鏈存在環：起自 {start}")
            seen.add(cur)
            cur = parents[cur]


def _validate_key_terms(draft: dict[str, Any]) -> None:
    """key_terms 須為 list（可空）；每項含非空 term 與 definition。"""
    terms = draft.get("key_terms")
    if not isinstance(terms, list):
        raise ClaimModelError("key_terms 關鍵 Claim 用語表必須為 list")
    for i, term in enumerate(terms):
        if not isinstance(term, dict):
            raise ClaimModelError(f"key_terms[{i}] 必須為 dict")
        name = term.get("term")
        if not isinstance(name, str) or not name.strip():
            raise ClaimModelError(f"key_terms[{i}] 缺少 term")
        definition = term.get("definition")
        if not isinstance(definition, str) or not definition.strip():
            raise ClaimModelError(f"key_terms[{i}] 缺少 definition")
