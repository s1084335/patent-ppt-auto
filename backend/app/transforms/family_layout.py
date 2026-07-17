"""國家佈局（現有保護口徑）的家族×國家展開——純函式，不碰 DB。

資料流（由 refresh_report_family_country CLI 驅動）：
    report_patent_base 逐列
        → 状态正規化（alive/dead/pending/unknown）
        → 存活非 EP 列貢獻 (family, country_code)
        → 存活 EP 列依 EPC 三規則展開成生效國
        → group by 家族聚合、同族同國去重（保留件數）
        → 完整性核對（同族明細 US/EP/JP/KR/CN 五桶 vs 實際撈到列數）
    輸出 FamilyLayoutResult（country_rows 進 report_family_country、
    quality_rows 進 report_family_quality、summary 給 CLI 印出）。

EP 三規則（2026-07-14 定案，見 .agents/context/decisions.md）：
    ① 成熟授權件：直接用 EPC有效國家 展開。
    ② 剛授權件隔離：無效國家為空 且 有效國數 >= 30 → 全指定國推定值，
       標「生效程序進行中」不併入國家統計（另計數呈現）。
    ③ 到期件：有效國清空（且状态為 dead），自然貢獻 0。

輸入列是 dict，鍵為本模組定義的 canonical key（見 build_family_country_dataset
docstring）；DB 欄名/xlsx 表頭的對應由呼叫端（refresh CLI、scan CLI）處理，
讓本模組可同時服務 DB refresh 與離線樣本掃描。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from backend.app.mappings.legal_status import (
    STATUS_ALIVE,
    STATUS_DEAD,
    STATUS_PENDING,
    STATUS_UNKNOWN,
    normalize_legal_status,
)
from backend.app.reports.map_runner import NON_COUNTRY_AUTHORITIES

# 同族明細（WIPS同族各國家文獻數量(申請為準)）固定 7 桶：US/EP/PCT/JP/KR/CN/etc。
# 可逐國核對的只有這 5 個國家桶；PCT 非國家、etc 是混合桶，皆不比對。
FAMILY_COUNT_COUNTRY_BUCKETS: tuple[str, ...] = ("US", "EP", "JP", "KR", "CN")

# 規則②門檻：EPC 指定國全集約 38-39 國，取 30 作為「全指定國推定值」的判定下限。
EP_TRANSITION_THRESHOLD = 30

# 明細 token 形如 "US-2"、"etc-1"（半形連字號，數量為非負整數）。
_COUNT_TOKEN_RE = re.compile(r"^([A-Za-z]+)\s*-\s*(\d+)$")


def split_pipe_codes(text: str | None) -> list[str]:
    """把豎線分隔的國家清單（如 "DE | ES | FR | GB"）切成大寫代碼 list。

    空值/純空白回空 list；去重保序（WIPS 資料理論上不重複，防禦性處理）。
    """
    if not text:
        return []
    seen: set[str] = set()
    codes: list[str] = []
    for token in text.split("|"):
        code = token.strip().upper()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def parse_family_country_counts(text: str | None) -> tuple[dict[str, int], list[str]]:
    """解析同族明細字串 "US-2 | EP-1 | PCT-0 | ... | etc-1" 為 {桶: 件數}。

    回傳 (counts, bad_tokens)：解析失敗的 token 原樣回報，不吞掉。
    """
    counts: dict[str, int] = {}
    bad_tokens: list[str] = []
    if not text:
        return counts, bad_tokens
    for token in text.split("|"):
        token = token.strip()
        if not token:
            continue
        match = _COUNT_TOKEN_RE.match(token)
        if match is None:
            bad_tokens.append(token)
            continue
        bucket = match.group(1).upper()
        if bucket == "ETC":
            bucket = "etc"
        counts[bucket] = counts.get(bucket, 0) + int(match.group(2))
    return counts, bad_tokens


def classify_ep_contribution(
    valid_codes: list[str],
    invalid_is_blank: bool,
    threshold: int = EP_TRANSITION_THRESHOLD,
) -> tuple[str, list[str]]:
    """存活 EP 件的 EPC 欄判定，回傳 (kind, 貢獻國家清單)。

    kind:
        "in_transition"  規則②：無效空 且 有效國數 >= threshold → 隔離不計
        "countries"      規則①：用有效國展開（可能為空 list = 貢獻 0）
        "missing"        有效與無效皆空 → 資料缺（精簡匯出），需現形
    """
    if not valid_codes and invalid_is_blank:
        return "missing", []
    if invalid_is_blank and len(valid_codes) >= threshold:
        return "in_transition", []
    return "countries", valid_codes


@dataclass
class FamilyCountryRow:
    """report_family_country 一列：某家族在某國家的現有保護。"""

    family_id: str
    country_code: str
    direct_patent_count: int = 0
    via_ep_count: int = 0
    family_incomplete: bool = False
    is_surrogate_family: bool = False


@dataclass
class FamilyQualityRow:
    """report_family_quality 一列：某家族的完整性核對與異常計數。"""

    family_id: str
    is_surrogate_family: bool = False
    member_rows: int = 0
    expected_counts_raw: str | None = None
    family_incomplete: bool = False
    incomplete_detail: dict[str, dict[str, int]] = field(default_factory=dict)
    unknown_status_count: int = 0
    pending_status_count: int = 0
    ep_in_transition_count: int = 0
    ep_missing_epc_count: int = 0
    non_country_row_count: int = 0
    bad_count_tokens: list[str] = field(default_factory=list)


@dataclass
class FamilyLayoutResult:
    """build_family_country_dataset 的輸出。"""

    country_rows: list[FamilyCountryRow]
    quality_rows: list[FamilyQualityRow]
    summary: dict[str, Any]


def _surrogate_family_id(patent_id: Any) -> str:
    """WIPS同族ID 為空時的替代家族 id：單件自成一族，不無聲丟掉真實保護。"""
    return f"P{patent_id}"


def build_family_country_dataset(
    rows: Iterable[Mapping[str, Any]],
    expand_ep: bool = False,
) -> FamilyLayoutResult:
    """把 report_patent_base 列展開成 家族×國家 與 家族品質 兩組資料。

    expand_ep（2026-07-15 定案：第一版 False）：
        False = 佈局做到申請國（受理局）層級——存活 EP 件以「EP」桶直接貢獻，
                不展開成 EPC 生效國（地圖端以區域標示呈現 EP）。
        True  = EP 件依 EPC 三規則展開成生效國（邏輯與測試保留，等資料到位再啟用）。

    輸入列的 canonical key（呼叫端負責從 DB 欄名/xlsx 表頭映射過來）：
        patent_id      任意可字串化的唯一值
        family_id      WIPS同族ID（可空）
        country_code   受理局代碼（可空）
        legal_status   状态原始值（可空）
        family_counts  WIPS同族各國家文獻數量(申請為準) 原始字串（可空）
        epc_valid      EPC有效國家[EP] 原始字串（可空，僅 expand_ep=True 時使用）
        epc_invalid    EPC無效國家[EP] 原始字串（可空，僅 expand_ep=True 時使用）
    """
    # family_id -> country_code -> [direct, via_ep]
    coverage: dict[str, dict[str, list[int]]] = {}
    quality: dict[str, FamilyQualityRow] = {}
    # 完整性核對用：family_id -> country_code -> 實際列數（不分状态，核對「有沒有撈齊」）
    actual_rows_by_country: dict[str, dict[str, int]] = {}

    status_totals = {STATUS_ALIVE: 0, STATUS_DEAD: 0, STATUS_PENDING: 0, STATUS_UNKNOWN: 0}

    for row in rows:
        raw_family = row.get("family_id")
        family_text = str(raw_family).strip() if raw_family is not None else ""
        is_surrogate = not family_text
        family_id = family_text or _surrogate_family_id(row.get("patent_id"))

        qrow = quality.get(family_id)
        if qrow is None:
            qrow = FamilyQualityRow(family_id=family_id, is_surrogate_family=is_surrogate)
            quality[family_id] = qrow
            coverage[family_id] = {}
            actual_rows_by_country[family_id] = {}
        qrow.member_rows += 1

        # 同族明細是家族層級常數，取家族內第一個非空值即可。
        family_counts_raw = row.get("family_counts")
        if qrow.expected_counts_raw is None and family_counts_raw and str(family_counts_raw).strip():
            qrow.expected_counts_raw = str(family_counts_raw).strip()

        country_raw = row.get("country_code")
        country = str(country_raw).strip().upper() if country_raw is not None else ""
        if country:
            per_country = actual_rows_by_country[family_id]
            per_country[country] = per_country.get(country, 0) + 1

        status = normalize_legal_status(row.get("legal_status"))
        status_totals[status] += 1
        if status == STATUS_UNKNOWN:
            qrow.unknown_status_count += 1
            continue
        if status == STATUS_PENDING:
            qrow.pending_status_count += 1
            continue
        if status == STATUS_DEAD:
            # 規則③：到期/失效不貢獻現有保護（EP 到期件有效國已清空，同樣落到這裡）。
            continue

        # 以下皆為 alive。
        if country == "EP":
            if not expand_ep:
                # 申請國層級：EP 以受理局桶直接貢獻，不展開生效國。
                slot = coverage[family_id].setdefault("EP", [0, 0])
                slot[0] += 1
                continue
            valid_codes = split_pipe_codes(row.get("epc_valid"))
            invalid_is_blank = not split_pipe_codes(row.get("epc_invalid"))
            kind, codes = classify_ep_contribution(valid_codes, invalid_is_blank)
            if kind == "in_transition":
                qrow.ep_in_transition_count += 1
                continue
            if kind == "missing":
                qrow.ep_missing_epc_count += 1
                continue
            for code in codes:
                slot = coverage[family_id].setdefault(code, [0, 0])
                slot[1] += 1
            continue

        if not country or country in NON_COUNTRY_AUTHORITIES:
            # WO 等區域受理局（或缺 country_code）無法歸到單一國家。
            qrow.non_country_row_count += 1
            continue

        slot = coverage[family_id].setdefault(country, [0, 0])
        slot[0] += 1

    # 完整性核對：明細五國桶 expected vs 實際撈到列數，不等即不完整（雙向都算）。
    for family_id, qrow in quality.items():
        if not qrow.expected_counts_raw:
            continue
        expected, bad_tokens = parse_family_country_counts(qrow.expected_counts_raw)
        qrow.bad_count_tokens = bad_tokens
        detail: dict[str, dict[str, int]] = {}
        for bucket in FAMILY_COUNT_COUNTRY_BUCKETS:
            expected_count = expected.get(bucket, 0)
            actual_count = actual_rows_by_country[family_id].get(bucket, 0)
            if expected_count != actual_count:
                detail[bucket] = {"expected": expected_count, "actual": actual_count}
        if detail:
            qrow.family_incomplete = True
            qrow.incomplete_detail = detail

    country_rows: list[FamilyCountryRow] = []
    for family_id, per_country in coverage.items():
        qrow = quality[family_id]
        for country, (direct, via_ep) in sorted(per_country.items()):
            country_rows.append(
                FamilyCountryRow(
                    family_id=family_id,
                    country_code=country,
                    direct_patent_count=direct,
                    via_ep_count=via_ep,
                    family_incomplete=qrow.family_incomplete,
                    is_surrogate_family=qrow.is_surrogate_family,
                )
            )

    quality_rows = sorted(quality.values(), key=lambda q: q.family_id)
    protected_countries = sorted({r.country_code for r in country_rows})
    summary: dict[str, Any] = {
        "family_count": len(quality_rows),
        "surrogate_family_count": sum(1 for q in quality_rows if q.is_surrogate_family),
        "family_country_rows": len(country_rows),
        "protected_country_count": len(protected_countries),
        "protected_countries": protected_countries,
        "incomplete_family_count": sum(1 for q in quality_rows if q.family_incomplete),
        "ep_in_transition_count": sum(q.ep_in_transition_count for q in quality_rows),
        "ep_missing_epc_count": sum(q.ep_missing_epc_count for q in quality_rows),
        "unknown_status_count": sum(q.unknown_status_count for q in quality_rows),
        "pending_status_count": sum(q.pending_status_count for q in quality_rows),
        "non_country_row_count": sum(q.non_country_row_count for q in quality_rows),
        "status_totals": status_totals,
    }
    return FamilyLayoutResult(country_rows=country_rows, quality_rows=quality_rows, summary=summary)
