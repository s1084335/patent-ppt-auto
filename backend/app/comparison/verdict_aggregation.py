"""案件比對 · verdict 彙總接線（純邏輯，無 DB）。

把兩個來源合併成 all-elements rule 可套用的輸入，再產 claim 級彙總：
- understanding（理解稿）：提供引用鏈——哪些是獨立項、哪些是從屬項、parent 是誰。
- element_analysis（逐要素比對）：提供每個 claim 各要素的四態（met/…）。

canceled/unknown claim 不納入比對彙總（零技術內容，定案通則）。
實際 all-elements rule 與引用鏈推論由 verdict.infer_verdicts 執行，本模組只做資料合併。
"""
from __future__ import annotations

from typing import Any

from backend.app.comparison.verdict import infer_verdicts


def _status_list(claim_elements: list[dict[str, Any]]) -> list[str]:
    """取一個 claim 各要素的四態值（保序）。"""
    return [element.get("status") for element in claim_elements]


def build_claims_for_inference(
    understanding: dict[str, Any],
    element_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    """合併理解稿引用鏈與逐要素四態，組成 infer_verdicts 可吃的 claims 清單。

    每項輸出：{claim_number, type: 'independent'|'dependent', parent?, element_statuses}。
    element_statuses 取自 element_analysis；獨立/從屬與 parent 取自 understanding。
    unknown_claims（canceled 等）不納入。element_analysis 缺某 claim 時該 claim
    element_statuses 為空，交由 infer_verdicts 依規則處理（獨立項空要素會被拒）。
    """
    # element_analysis 的四態依 claim_number 建索引，供合併時查表
    status_by_num: dict[str, list[str]] = {}
    for claim in (element_analysis or {}).get("claims", []):
        num = str(claim.get("claim_number", "")).strip()
        if num:
            status_by_num[num] = _status_list(claim.get("elements", []))

    claims: list[dict[str, Any]] = []
    for indep in (understanding or {}).get("independent_claims", []):
        num = str(indep.get("claim_number", "")).strip()
        claims.append({
            "claim_number": num,
            "type": "independent",
            "element_statuses": status_by_num.get(num, []),
        })
    for dep in (understanding or {}).get("dependent_claims", []):
        num = str(dep.get("claim_number", "")).strip()
        claims.append({
            "claim_number": num,
            "type": "dependent",
            "parent": str(dep.get("parent", "")).strip(),
            "element_statuses": status_by_num.get(num, []),
        })
    # unknown_claims 一律不納入彙總
    return claims


def aggregate_verdict(
    understanding: dict[str, Any],
    element_analysis: dict[str, Any],
) -> dict[str, Any]:
    """產 claim 級彙總結果：{claims: [{claim_number, status, inferred}]}。

    status 為 ClaimStatus 的字串值（not_established/possibly_established/needs_review），
    格式對齊 ComparisonStore.save_verdict 的寫入層 claim 狀態驗證。
    """
    claims = build_claims_for_inference(understanding, element_analysis)
    results = infer_verdicts(claims)
    return {
        "claims": [
            {
                "claim_number": num,
                "status": res["status"].value,
                "inferred": res["inferred"],
            }
            for num, res in results.items()
        ]
    }
