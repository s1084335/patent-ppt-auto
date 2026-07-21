"""案件比對 · Claim 全文解析器（純邏輯，無 DB）。

把「所有權利要求」全文切成結構化 claims，接通 claim_source → parser → claim_model 骨架鏈。

切條：以條號為錨（行首或 ` | ` 分隔的段首）。真實 importcheck 全文以 ` | ` 分隔各條，
另相容換行分隔。條號支援英文「1.」與中文「1.／1、／1．」。
引用判定支援雙語：
- 英文：`... according to claim 1`、`as claimed in claim 2`、`claim 10 or claim 11`、`any of claims 1-3`。
- 中文（簡體）：`根据权利要求1所述`、`如权利要求2所述`、`权利要求1或3`（含傳統「權利要求」）。

誠實優先：引用判定失敗（前向引用、抓不到條號、canceled 範圍）標 type='unknown' 並保留原文，
不猜 parent、不丟棄文字，交後續 AI 理解階段人工補。欄位名對齊 claim_model／verdict 契約。
"""
from __future__ import annotations

import re
from typing import Any

# 段首條號錨：可選範圍（2-40），分隔符 . ． 、，其後為非數字（避免誤切 "1.5 mm"）
_ANCHOR = re.compile(r"^\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*[.．、]\s*(\D.*)$", re.S)
# 引用 token：claim(s)/权利要求/權利要求 後接數字（可含 range/列舉連接詞）
_REF = re.compile(
    r"(?:claims?|权利要求|權利要求)\s*(\d+(?:\s*(?:[-–~,、]|to|至|and|or|或|和)\s*\d+)*)",
    re.IGNORECASE,
)
_CANCELED = re.compile(r"cancell?ed", re.IGNORECASE)
_RANGE = re.compile(r"[-–~]|to|至", re.IGNORECASE)


def _split_segments(text: str) -> list[str]:
    """以 ` | ` 或換行切段；不以錨開頭者併回前一段（續行）。"""
    parts = re.split(r"\s*\|\s*|\r?\n", text)
    segments: list[str] = []
    for part in parts:
        if _ANCHOR.match(part):
            segments.append(part.strip())
        elif segments:
            segments[-1] = segments[-1] + " " + part.strip()
        # 錨前的前言（無 current）忽略
    return segments


def _ref_numbers(body: str) -> list[int]:
    """抽出 body 內所有引用的 claim 數字（去重、展開 range）。"""
    nums: list[int] = []
    for token in _REF.findall(body):
        found = [int(x) for x in re.findall(r"\d+", token)]
        if len(found) == 2 and _RANGE.search(token):
            nums.extend(range(min(found), max(found) + 1))  # range 展開
        else:
            nums.extend(found)
    seen: set[int] = set()
    ordered: list[int] = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def parse_claims(all_claims_text: str) -> list[dict[str, Any]]:
    """把權利要求全文解析成 claims 清單。

    每項：{claim_number, text, type, parent, parents, multiple_dependent}。
    type ∈ independent/dependent/unknown；unknown 保留原文、parent=None、parents=[]。
    """
    claims: list[dict[str, Any]] = []
    for segment in _split_segments(all_claims_text or ""):
        m = _ANCHOR.match(segment)
        if not m:
            continue
        start, end, body = m.group(1), m.group(2), m.group(3).strip()
        number = f"{start}-{end}" if end else start
        cur = int(start)

        # canceled 或範圍條號：不是可分類的實質 claim → unknown（保留原文）
        if end is not None or _CANCELED.search(body):
            claims.append(_mk(number, segment, "unknown"))
            continue

        refs = [n for n in _ref_numbers(body)]
        if not refs:
            claims.append(_mk(number, segment, "independent"))
            continue
        backward = [n for n in refs if n < cur]  # 只認向前（引用較小編號）的引用
        if not backward:
            # 有引用詞但無有效 parent（前向引用等）→ 不猜，標 unknown
            claims.append(_mk(number, segment, "unknown"))
            continue
        parents = [str(n) for n in backward]
        if len(parents) == 1:
            claims.append(_mk(number, segment, "dependent", parent=parents[0], parents=parents))
        else:
            claims.append(_mk(number, segment, "dependent", parents=parents, multiple=True))
    return claims


def _mk(number: str, text: str, ctype: str, parent: str | None = None,
        parents: list[str] | None = None, multiple: bool = False) -> dict[str, Any]:
    """組一個 claim 條目（欄位名對齊 claim_model／verdict）。"""
    return {
        "claim_number": number,
        "text": text.strip(),
        "type": ctype,
        "parent": parent,
        "parents": parents or [],
        "multiple_dependent": multiple,
    }


def to_understanding_skeleton(claims: list[dict[str, Any]], source_fields: list[str]) -> dict[str, Any]:
    """把 parse_claims 結果轉成 claim_model 理解稿骨架（elements 待 AI 理解階段填）。

    欄位名對齊 claim_model 契約；unknown 條目另放 unknown_claims 供人工補判，不硬塞獨立/從屬。
    """
    independent, dependent, unknown = [], [], []
    for c in claims:
        entry = {"claim_number": c["claim_number"], "text": c["text"], "elements": []}
        if c["type"] == "independent":
            independent.append(entry)
        elif c["type"] == "dependent":
            dependent.append({**entry, "parent": c["parent"], "parents": c["parents"],
                              "multiple_dependent": c["multiple_dependent"]})
        else:
            unknown.append(entry)
    return {
        "source_fields": source_fields,
        "independent_claims": independent,
        "dependent_claims": dependent,
        "unknown_claims": unknown,
        "key_terms": [],
    }
