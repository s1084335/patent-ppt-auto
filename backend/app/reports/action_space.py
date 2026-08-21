"""行動候選池與逐主題掃描（tasks §9.7，2026-08-19 使用者設計）。

## 使用者的流程

    資料分析 → 掃描所有可成立的行動方向 → 每個方向判斷「成立／不成立／證據不足」
             → 只輸出成立的專利行動

**行動數量不限，但行動空間必須完整掃描。** Verifier 檢查的不是「有沒有寫 ≥4 個」，
而是「所有候選方向是不是都評估了」——`covered N/N` 就 PASS，即使最後只有 2 個成立。

## 入池條件：能寫出一條只用引擎欄位的判定規則

⚠ 使用者裁決「候選池只放判得出來的」。判不出來的**明文列進 `KNOWN_GAPS`**，
不進池、也**不得降級成「證據不足」**——

| | 語意 | 補資料有沒有用 |
|---|---|---|
| 證據不足 | 這**份資料**不夠 | 有 |
| 已知未涵蓋 | **系統**沒有這個判準 | 沒有 |

混在一起會製造一個很貴的誤解：使用者看到「證據不足」會去補資料，補完還是
證據不足，而真正的原因是引擎沒這個能力，且沒有任何地方寫著。

## 判定由引擎算，但規則是**地板不是天花板**

規則全是確定性的，引擎算得出來——保證不漏掃、零 token、`covered` 恆等成立，
Verifier 不必再防 LLM 偷懶（那個風險從根上消失）。

⚠ 但「交給機械」會犯**另一種**上世代的錯：規則寫死之後，規則沒涵蓋的真實機會
**永遠不會出現且不報錯**——那正是 v5／v7／v9 形式鎖的核心機制（不是「鎖住格式」，
是「用一條規則決定了什麼能存在」）。所以 CLI 可加可否決（§9.7e-1），
本模組只提供**候選**判定。

## 行動落在「下一步查證動作」的層級

沿用 `chart_runner._qlabel` 2026-08-02 的先例：那張圖只有件數×家數兩個維度，
推不出迴避設計結論，故改為描述**現象**與**下一步查證動作**。同一條界線適用這裡
——「FTO／侵權風險調查」寫成查證動作成立，寫成結論才不成立。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

#: 三值判定（🔴 封閉集合，消費端據此驗證）
HOLDS = "成立"
FAILS = "不成立"
UNKNOWN = "證據不足"
VERDICTS = (HOLDS, FAILS, UNKNOWN)


@dataclass(frozen=True)
class Thresholds:
    """本批推導出的門檻（🔴 2026-08-20 使用者裁決：邏輯用中位數，數字不得寫死）。

    ⚠ 做成物件而非多個位置參數：之後再加一個推導門檻時，不必改 10 條規則的簽章
    ——那種改動每次都會漏掉一兩條，而漏掉的那條會靜默沿用舊行為。

    ⚠ `median_max_share` 可為 `None`（空批算不出來）。消費端必須把它當成
    「沒有尺」回 `UNKNOWN`，**不得**當成 0 而讓所有比較都成立。
    """

    median_count: float
    median_max_share: float | None = None


def derive_thresholds(rows: list[dict[str, Any]]) -> Thresholds:
    """從**傳進來的那批**主題算出門檻。

    ⚠ 與 `build_opportunity_matrix` 的四象限同一套作法（中位數切高低），
    這樣「集中」在整個系統只有一個定義。原本 `max_share >= 50` 是第二個定義：
    實測 `max_share` 中位數在滑雪機技術是 40、割草機技術是 56.5——固定 50 在
    前者是「前 20%」、在後者是「前 60%」，**同一個數字兩批講的不是同一件事**。
    """
    import statistics

    counts = [float(r.get("patent_count") or 0) for r in rows]
    shares = [float(r["max_share"]) for r in rows
              if r.get("max_share") is not None]
    return Thresholds(
        median_count=statistics.median(counts) if counts else 0.0,
        median_max_share=statistics.median(shares) if shares else None,
    )


@dataclass(frozen=True)
class ActionCandidate:
    """一個候選行動方向。

    `rule` 收 `(row, thresholds)` 回三值之一。⚠ 缺欄位時必須回 `UNKNOWN`
    而不是 `FAILS`：「這份資料判不出來」與「判出來不成立」是不同的結論，
    給使用者的下一步也不同（補資料 vs 不用管）。
    """

    rule: Callable[[dict[str, Any], Thresholds], str]
    purpose: str        # 這個行動是什麼意思（CLI 要據此寫判讀）
    signal: str         # 它讀哪些引擎欄位（讓判定可反查）


def _status(row: dict[str, Any]) -> str | None:
    v = row.get("status")
    return str(v) if v else None


def _num(row: dict[str, Any], key: str) -> float | None:
    v = row.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _by_status(*wanted: str) -> Callable[[dict, Thresholds], str]:
    """狀態在指定集合內即成立；沒有狀態＝這份資料判不出來。"""
    def rule(row: dict[str, Any], _t: Thresholds) -> str:
        s = _status(row)
        if s is None:
            return UNKNOWN
        return HOLDS if s in wanted else FAILS
    return rule


def _rule_explore(row: dict[str, Any], t: Thresholds) -> str:
    s = _status(row)
    count = _num(row, "patent_count")
    if s is None or count is None:
        return UNKNOWN
    if s == "件數不足":
        return HOLDS
    return HOLDS if (s == "新興" and count < t.median_count) else FAILS


def _rule_maintain(row: dict[str, Any], t: Thresholds) -> str:
    s = _status(row)
    count = _num(row, "patent_count")
    if s is None or count is None:
        return UNKNOWN
    return HOLDS if (s == "成熟" and count >= t.median_count) else FAILS


def _rule_differentiate(row: dict[str, Any], _t: Thresholds) -> str:
    s = _status(row)
    q = row.get("quadrant")
    if s is None and q is None:
        return UNKNOWN
    if s in ("成熟", "競爭集中") or q == "多方投入技術":
        return HOLDS
    return FAILS


def _rule_track_pending(row: dict[str, Any], _t: Thresholds) -> str:
    """他人審查中件數 ≥1 → 值得追蹤。⚠ 這是**外部訊號**（對手給的時間壓力），
    可查證；不是我們假設的月份。
    """
    n = _num(row, "pending_count")
    if n is None:
        return UNKNOWN
    return HOLDS if n >= 1 else FAILS


def _by_quadrant(*wanted: str) -> Callable[[dict, Thresholds], str]:
    def rule(row: dict[str, Any], _t: Thresholds) -> str:
        q = row.get("quadrant")
        if not q:
            return UNKNOWN
        return HOLDS if q in wanted else FAILS
    return rule


def _rule_concentration(row: dict[str, Any], t: Thresholds) -> str:
    """權利集中程度——門檻取**本批** `max_share` 的中位數，不寫死。

    🔴 2026-08-20 使用者裁決：「機會四象限數字都是中位數，所以邏輯用中位數
    但數字不能寫死」。原本是 `share >= 50`。

    ⚠ 50 不只是「沒有依據」，它讓同一個數字在兩批講不同的事：實測 `max_share`
    中位數在滑雪機技術是 **40**、割草機技術是 **56.5**——固定 50 在前者是
    「前 20%」（5 個主題只過 1 個）、在後者是「前 60%」（10 個過 6 個）。
    ⚠ 而且「集中」原本有兩個定義：象限的「集中持有」是中位數推導的，
    這裡的 50 是另一個。改用中位數後整個系統只有一個定義。

    ⚠ 誠實記錄另一種讀法：50% 有「單一持有人過半」的絕對意義。但本規則的用途
    是排序與比較（值不值得追這個主題的集中度），比較型判準就該用本批的尺。
    """
    q = row.get("quadrant")
    share = _num(row, "max_share")
    if q == "集中持有":
        return HOLDS                       # 象限已判定，不必再看 share
    if q is None and share is None:
        return UNKNOWN
    if share is None or t.median_max_share is None:
        # ⚠ 沒有尺就量不了 → 證據不足，不得放行也不得判不成立
        return UNKNOWN
    return HOLDS if share >= t.median_max_share else FAILS


def _rule_niche(row: dict[str, Any], _t: Thresholds) -> str:
    """§9.7d：「技術空白」改成「利基」。

    ⚠ 用詞不是修辭問題：「空白」是**缺席主張**（這裡沒有人做），而母體只是
    匯入的公開專利——沒出現可能是沒人做、可能是檢索沒撈到、可能還沒公開。
    拿「沒看到」當「不存在」撐不住。「利基」是**在場主張**（有人做但很少），
    資料看得到。⚠ 但資料只說低密度、說不出有價值，故行動寫成「評估」。
    """
    q = row.get("quadrant")
    count = _num(row, "patent_count")
    if q is None or count is None:
        return UNKNOWN
    return HOLDS if (q == "低件數·少申請人" and count >= 1) else FAILS


#: 🔴 行動候選池的**唯一定義處**（比照 `LAYOUTS`／`ACTION_VERBS`）。
#:
#: ⚠ 每項的 `rule` 只讀引擎欄位——寫不出這樣一條規則的方向不進池（見 `KNOWN_GAPS`）。
ACTION_POOL: dict[str, ActionCandidate] = {
    "優先投入": ActionCandidate(
        _by_status("新興", "成長"),
        "增加該方向的研發資源", "status ∈ {新興, 成長}"),
    "差異化開發": ActionCandidate(
        _rule_differentiate,
        "不跟主流方案正面重複，改變技術手段或功能組合",
        "status ∈ {成熟, 競爭集中} ∨ 象限＝多方投入技術"),
    "探索性研發": ActionCandidate(
        _rule_explore,
        "做 PoC、原型、小規模研究，不立即重押",
        "status＝件數不足，或（新興 ∧ patent_count < 中位數）"),
    "維持／漸進改善": ActionCandidate(
        _rule_maintain,
        "不做大幅突破，集中在成本、性能、可靠性等漸進創新",
        "status＝成熟 ∧ patent_count ≥ 中位數"),
    "降低投入": ActionCandidate(
        _by_status("衰退"),
        "研發資源逐步轉至更具機會的方向", "status＝衰退"),
    "追蹤他人審查中案件": ActionCandidate(
        _rule_track_pending,
        "盯住對手尚未確定的權利範圍，那是可查證的外部時間壓力",
        "pending_count ≥ 1（§7e 法律狀態分解）"),
    "檢視請求項範圍重疊": ActionCandidate(
        _by_quadrant("多方投入技術"),
        "多方投入的技術區先做 claim overlap 分析（查證動作，非侵權結論）",
        "象限＝多方投入技術"),
    "覆核代表專利": ActionCandidate(
        _by_quadrant("低件數·少申請人"),
        "件數少時統計不可靠，改人工讀代表案", "象限＝低件數·少申請人"),
    "確認權利集中程度": ActionCandidate(
        _rule_concentration,
        "確認權利是否集中在單一持有者，影響可繞開的空間",
        "象限＝集中持有 ∨ max_share ≥ 本批中位數"),
    "評估利基切入": ActionCandidate(
        _rule_niche,
        "低密度且少玩家的區塊值不值得切入——**評估**，不是斷定它是機會",
        "象限＝低件數·少申請人 ∧ patent_count ≥ 1"),
}

#: 🔴 已知未涵蓋：判不出來，明文列出、不進池、**不得降級成「證據不足」**。
KNOWN_GAPS: tuple[dict[str, str], ...] = (
    {"action": "加速深化",
     "missing": "「已有**自身**基礎」",
     "why_not": "需要「本公司」一級概念；使用者 2026-08-18 裁決這兩輪不做"},
    {"action": "技術重組／跨域整合",
     "missing": "「**相鄰技術**出現連結」",
     "why_not": "需主題×主題關聯度，引擎沒有這個計算"
                "（clustering/model.py 的 cosine_similarity 是文件對主題）"},
    {"action": "轉向（被取代）",
     "missing": "「被其他技術**取代**」",
     "why_not": "需跨主題替代訊號，要的是因果方向不只相關；"
                "其前半「衰退」已入池為『降低投入』"},
)


def scan_topic(row: dict[str, Any], thresholds: Thresholds) -> dict[str, str]:
    """對**每一個**候選方向給判定（🔴 完整掃描的實作）。

    ⚠ 一律走完整個池子，不提早 return——`covered` 恆等成立就是靠這個迴圈。
    """
    return {key: item.rule(row, thresholds) for key, item in ACTION_POOL.items()}


def holding_actions(row: dict[str, Any], thresholds: Thresholds) -> list[str]:
    """只回**成立**的行動；數量由資料決定（可能是 1 個，也可能是 6 個）。"""
    return [k for k, v in scan_topic(row, thresholds).items() if v == HOLDS]


def scan_workspace(rows: list[dict[str, Any]], thresholds: Thresholds) -> dict[str, Any]:
    """整批掃描並附對帳。

    ⚠ `covered` 是 `N/N` 而不是 `寫了幾個/總共幾個`：引擎不會漏掃自己的迴圈，
    所以這個數字的用途是**證明掃描器真的跑完整個池**，不是防偷懶
    ——防偷懶那件事在引擎判定的設計下從根上消失了。
    """
    per_topic = {}
    for row in rows:
        per_topic[str(row.get("label") or row.get("topic_code") or "")] = \
            scan_topic(row, thresholds)
    return {
        "covered": f"{len(ACTION_POOL)}/{len(ACTION_POOL)}",
        "verdicts": per_topic,
        "known_gaps": [dict(g) for g in KNOWN_GAPS],
    }
