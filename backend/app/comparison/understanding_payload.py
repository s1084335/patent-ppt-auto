"""案件比對 · 理解稿 payload 組裝與入庫守門（純邏輯，DB 只經 comparison_store）。

- build_understanding_payload：把 claim 原文＋parser 骨架＋模板指令組成確定性 payload，
  供 CLI／未來 Companion 餵 LLM（見 .agents/skills/claim-understanding-flow.md）。
- save_understanding_draft：draft 入庫前守門——validate_understanding 通過、且 draft 的
  claim_number 集合與 payload 一致（AI 不得增刪條目）才准存；附 ai_model／prompt_version。
"""
from __future__ import annotations

import re
from typing import Any

from backend.app.comparison.claim_model import validate_understanding
from backend.app.comparison.claim_parser import parse_claims, to_understanding_skeleton

PROMPT_VERSION = "claim_understanding_v2"

# 獨立項最少要素數（前言＋≥2 結構要件）；確實極短的 claim 可低於此值但須附 single_element_reason
MIN_INDEPENDENT_ELEMENTS = 3

# 餵給 LLM 的硬規則（與 claim-understanding-flow.md v2 同步；改此處必須升版）
INSTRUCTIONS = (
    "依 claim 原文產理解稿 JSON：分析全部獨立項與從屬鏈。要素拆解＝最小可獨立判斷的技術特徵單位："
    "獨立項必拆＝前言(preamble)一要素＋comprising/包括 後每個結構要件各一要素＋獨立功能/連接限定各一要素"
    "（拆分錨點＝分號、逐項列舉、wherein 子句），獨立項 elements 至少 3；確極短者可低於 3 但須附 "
    "single_element_reason。從屬項每個新增限制一要素（通常 1–2）。element.text 必為 claim 原文逐字片段"
    "（不改寫、不翻譯），全體 element.text 依序拼接應可還原 claim 主體（容連接詞縫隙）；explanation 繁中"
    "說明技術意義與關鍵用語。key_terms 只收 claim 內用語；parser 標 unknown 者原樣保留於 unknown_claims "
    "標「需人工釐清」不猜；不得增刪 claim；不下侵權／有效性／法律結論。prompt_version=" + PROMPT_VERSION
)

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """正規化空白供 substring 比對（claim 原文與 element.text 可能有換行/多空白差異）。"""
    return _WS.sub(" ", text or "").strip()


class DraftGuardError(ValueError):
    """理解稿入庫守門失敗（AI 增刪了 claim 條目，與 payload 不一致）。"""


def build_understanding_payload(patent: dict[str, Any]) -> dict[str, Any]:
    """組確定性理解稿 payload。

    patent：{patent_number, claim_text, source_fields}。
    回傳含 skeleton（parser 骨架）、claims_raw（全部 claims 含 unknown 標記）、指令與版本。
    """
    claim_text = patent.get("claim_text") or ""
    source_fields = list(patent.get("source_fields") or [])
    claims = parse_claims(claim_text)
    skeleton = to_understanding_skeleton(claims, source_fields)
    return {
        "patent_number": patent.get("patent_number"),
        "source_fields": source_fields,
        "prompt_version": PROMPT_VERSION,
        "instructions": INSTRUCTIONS,
        "skeleton": skeleton,
        "claims_raw": claims,  # 全部條目（含 unknown），供守門比對集合
    }


def _draft_claim_numbers(draft: dict[str, Any]) -> set[str]:
    """理解稿 draft 內所有 claim_number（獨立＋從屬＋unknown）。"""
    numbers: set[str] = set()
    for key in ("independent_claims", "dependent_claims", "unknown_claims"):
        for c in draft.get(key, []) or []:
            if c.get("claim_number"):
                numbers.add(c["claim_number"])
    return numbers


def save_understanding_draft(
    store: Any, run_id: int, payload: dict[str, Any], draft: dict[str, Any], ai_model: str
) -> int:
    """守門後存理解稿。

    1. validate_understanding(draft) 通過（結構／來源白名單），否則拋 ClaimModelError。
    2. draft 的 claim_number 集合須等於 payload.claims_raw 集合，否則拋 DraftGuardError。
    附 ai_model／prompt_version 後交 store.save_understanding 版本化保存，回傳版本號。
    """
    validate_understanding(draft)  # 不合法直接拋 ClaimModelError
    payload_numbers = {c["claim_number"] for c in payload.get("claims_raw", [])}
    draft_numbers = _draft_claim_numbers(draft)
    if draft_numbers != payload_numbers:
        raise DraftGuardError(
            f"理解稿 claim 集合與 payload 不一致：多={draft_numbers - payload_numbers}，"
            f"缺={payload_numbers - draft_numbers}")

    # v2 要素拆解守門：substring 校驗＋獨立項最少要素數
    raw_by_num = {c["claim_number"]: _norm(c.get("text", "")) for c in payload.get("claims_raw", [])}
    for key in ("independent_claims", "dependent_claims"):
        for c in draft.get(key, []) or []:
            raw = raw_by_num.get(c["claim_number"], "")
            for el in c.get("elements", []):
                if _norm(el.get("text", "")) not in raw:
                    raise DraftGuardError(
                        f"claim {c['claim_number']} 的 element.text 非 claim 原文片段："
                        f"{el.get('text', '')[:40]!r}")
    for c in draft.get("independent_claims", []) or []:
        n = len(c.get("elements", []))
        if n < MIN_INDEPENDENT_ELEMENTS and not str(c.get("single_element_reason") or "").strip():
            raise DraftGuardError(
                f"獨立項 claim {c['claim_number']} 只有 {n} 個 element（需 ≥{MIN_INDEPENDENT_ELEMENTS}），"
                f"且未附 single_element_reason")

    stored = {**draft, "ai_model": ai_model, "prompt_version": payload["prompt_version"]}
    return store.save_understanding(run_id, stored)
