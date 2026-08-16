"""母體對帳器（A3，2026-08-06）：算出每張報表的分析母體並組成頁尾註記。

## 為什麼要有這一層

實測 15 張報表有 **6 張母體 ≠ 55**：IPC 44／CPC 5／權人 36／家族 48／
功效分群 44／技術分群 35。封面寫 55、各頁各說各話，而**沒有一頁解釋**
——讀者只會認為資料錯誤。`chart_runner` 全檔 0 次提到「母體」，`build_ppt` 僅 1 次。

## 這是唯一定義處

母體數字與排除原因**只在這裡算一次**，頁尾註記與「讀圖須知」頁共用同一份輸出。
⚠ 不得讓兩處各算一份——那正是本專案反覆踩到的「同一份知識兩個落點」，
症狀會是「讀圖須知說 19 件無權人，權人頁尾說母體 36/55」而兩邊都對不起來。

## 為什麼不另外查一次 DB

每張報表的 rows 都已經帶 `patent_count`，加總即為該報表涵蓋的件數——
**零額外查詢**。各報表語意不同（展開 VIEW 是涉入件數、家族頁是同族合併後件數）
正是要靠「原因」文字交代，不是靠改算法。
"""
from __future__ import annotations

from typing import Any

from backend.app.clustering.sources import SOURCE_SEGMENT_SLUGS

# 專利總數的來源報表。
# ⚠ 為什麼是申請趨勢：每件專利恰好落在一個申請年，逐年加總即總數；
# 這也是封面統計卡既有的算法（`build_ppt._cover_stats`），沿用同一條路不另立。
TOTAL_SOURCE_REPORT = "application_trend"

# 各報表母體與總數不符的**原因**（唯一定義處）。
# ⚠ 沒登記的報表只印數字、不編理由——「母體 40/55 件」比「母體 40/55 件（原因不明）」誠實。
POPULATION_REASONS: dict[str, str] = {
    # 設計案的分類欄裝的是洛迦諾碼，非 IPC 體系；`IPC_LIKE_PATTERN` 早已擋掉。
    "ipc_main_distribution": "排除外觀設計 {excluded}",
    "cpc_main_distribution": "{excluded} 件無 CPC 分類",
    # ⚠ 同族合併後仍是「件」（2026-08-05 單位定案），故與其他頁同句型，不寫「家族 48 個」。
    "family_country_layout": "同族合併後",
    # 分群兩通道：技術缺無獨立項者、功效缺設計案。
    "cluster_topic_table": "{excluded} 件無分群來源文本",
    # ⚠ 2026-08-09 補：未授權公告的專利沒有公告年，母體**必然**小於總數。
    # 先前沒登記，讀者看到「母體 40/55 件」只會認為資料錯誤——那正是本模組
    # 當初要解決的問題本身。
    "publication_trend": "{excluded} 件尚未授權公告",
    "country_distribution": "{excluded} 件無受理局資訊",
}

#: 單位不是「件」的報表。
#: ⚠ 機會四象限的一個點是**一個主題**不是一件專利——沿用件數句型會產出
#: 「母體 7/55 件」這種語意錯誤的註記。這類報表不套用專利母體對帳。
POPULATION_REASONS["design_protection_detail"] = (
    "外觀策略只覆蓋可判定申請人與文獻種類的外觀/技術交叉資料"
)

NON_PATENT_UNIT_REPORTS = frozenset({
    "opportunity_quadrant",
})

#: 母體恆等於專利總數的報表（每件專利都會落進去，不需要理由）。
#: ⚠ 明列出來而不是「沒登記就當作相等」：沒登記代表「沒有人檢查過它的母體
#: 對不對」，那與「檢查過，確認相等」是兩回事。
SAME_AS_TOTAL_REPORTS = frozenset({
    "application_trend",
    # 待 2.3 與 kp_quadrant 一起重新設計；屆時重新歸類。
    "applicant_strength_profile",
})

# 申請人三報表走展開 VIEW，件數總和**會大於**專利總數（共同申請一件算兩家）。
# ⚠ 這是刻意的（0042 定案：專利分析慣例），但必須加註，否則讀者以為算錯。
OVER_COUNTING_REPORTS = frozenset({
    "applicant_ranking",
    "applicant_country_distribution",
    "applicant_year_matrix",
})
OVER_COUNTING_NOTE = "含共同申請，總和大於專利件數"


def patent_total(report_data: dict[str, Any]) -> int:
    """專利總數（單一定義處）。拿不到回 0，由呼叫端決定不印。

    ⚠ 不得在拿不到時猜一個數字——「母體 44/0 件」比不印更誤導。
    """
    report = (report_data.get("reports") or {}).get(TOTAL_SOURCE_REPORT) or {}
    return _sum_patent_count(report.get("rows") or [])


def _sum_patent_count(rows: list[dict[str, Any]]) -> int:
    """rows 的 `patent_count` 加總；非數字一律當 0，不讓髒值把整頁弄掛。"""
    total = 0
    for row in rows:
        value = row.get("patent_count")
        if isinstance(value, bool):  # bool 是 int 的子型別，先擋掉
            continue
        if isinstance(value, int):
            total += value
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            total += int(value)
    return total


def population_note(report_key: str, rows: list[dict[str, Any]], total: int) -> str:
    """組「母體 X/Y 件（原因）」。總數未知時回空字串。

    🔴 母體＝總數時**也要印**：省略會讓讀者無從分辨「這頁是全量」與「這頁忘了標」。
    """
    if total <= 0:
        return ""
    covered = _sum_patent_count(rows)
    head = f"母體 {covered}/{total} 件"

    if report_key in OVER_COUNTING_REPORTS:
        return f"{head}（{OVER_COUNTING_NOTE}）"

    template = POPULATION_REASONS.get(report_key)
    if not template or covered >= total:
        return head
    return f"{head}（{template.format(excluded=total - covered)}）"


# 分通道報表：rows 是技術＋功效**兩通道合併**的一份 list，每列帶 `source_field`。
# 🔴 2026-08-06 實機驗出：不拆通道就會得到「母體 79/55 件」（35+44）掛在
# 只呈現單一通道的 7 頁上，而且 79 > 55 又沒有過計數說明，讀者只會判定報表算錯。
# ⚠ 這兩個 report_key 在 PPT 端本來就是**一通道一頁**（`SPLIT_PAIR_REPORTS`），
# 母體卻是整包算——A3 初版只涵蓋「一個 report_key 對一個母體」，漏了這種形狀。
CHANNEL_SPLIT_REPORTS = frozenset({"cluster_topic_table", "opportunity_quadrant"})
CHANNEL_FIELD = "source_field"


def _channel_notes(report_key: str, rows: list[dict[str, Any]], total: int) -> dict[str, str]:
    """把分通道報表拆成 `report_key:slug` 逐通道註記。

    ⚠ 只產逐通道鍵、**不產合併鍵**：合併鍵的數字是錯的，寧可讓消費端拿不到而不印，
    也不要印一個會誤導的母體（沿本模組「拿不到就不印」的既有原則）。
    """
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        slug = SOURCE_SEGMENT_SLUGS.get(row.get(CHANNEL_FIELD))
        if slug:
            by_channel.setdefault(slug, []).append(row)
    return {
        f"{report_key}:{slug}": note
        for slug, channel_rows in by_channel.items()
        if (note := population_note(report_key, channel_rows, total))
    }


def population_notes(reports: dict[str, Any]) -> dict[str, str]:
    """一次算出全部報表的母體註記，供 `report_data["population"]` 落檔。

    ⚠ **引擎算、PPT 消費**：`build_ppt` 是會佈署到使用者機器的可攜 skill，
    不能 import 本模組，故母體只能由引擎算好寫進 `report_data`
    （全域規則「跨部署單元改走一方產生、一方消費」）。

    ⚠ 分通道報表的鍵是 `report_key:slug`（slug 來自 `clustering.sources` 的唯一
    定義處，與圖檔名後綴同一份）。消費端若解析不出通道就拿不到註記——
    這是刻意的，見 `_channel_notes`。
    """
    total = _sum_patent_count(
        ((reports.get(TOTAL_SOURCE_REPORT) or {}).get("rows")) or [])
    if total <= 0:
        return {}
    notes: dict[str, str] = {}
    for name, report in reports.items():
        rows = report.get("rows") or []
        if name in CHANNEL_SPLIT_REPORTS:
            notes.update(_channel_notes(name, rows, total))
            continue
        if note := population_note(name, rows, total):
            notes[name] = note
    return notes


def compose_footnote(population: str, *, sources: str, period: str) -> str:
    """組頁尾整行。**母體排最前**。

    🔴 為什麼排最前：`_fit_text` 截斷是砍尾巴。母體若排在「來源／期間」之後，
    版面一擠就會被砍掉，而**頁面看起來完全正常**——讀者不會知道少了什麼。

    ⚠ 濃縮（實測 55 字 → 41 字）：`資料來源：`→`來源：`、`統計期間：`→`期間`。
    頁尾實測容量 `12.13in × 0.22in @ 12pt` ＝ **單行約 72 中文字**，
    而 `sources` 是變數（4 頁掛兩個 report_key、最長報表名 11 字），
    故濃縮只是降低觸發機率，**排序才是護欄**。
    """
    parts = [population] if population else []
    parts.append(f"來源：{sources}" if sources else "來源：本次報表版本")
    parts.append(f"期間 {period}" if period else "期間 未標示")
    return "｜".join(parts)
