"""專利種類三分法（發明／新型／設計）——**唯一定義處**（A4，2026-08-06）。

## 🔴 判定只能用 `document_kind`，不能用 `patent_type`

`patent_type` 的 WIPS 來源欄是 **`发明专利/实用新型`**（`mappings/wips.py:252`）
——欄名就寫明它只有兩值，**11 件設計案全被歸進 `P`**。實測 55 件：

    patent_type='P' 共 28 件，其中 11 件是設計案 → **實際發明只有 17 件**

用 `patent_type` 判定會把設計案當成發明。這不是資料髒，是那一欄的值域本來就只有兩類。

**第二佐證**：設計案的 IPC 欄裝的是**洛迦諾碼**（21-02 ×10、19-07 ×1），不是 IPC；
`report_definitions` 的 `IPC_LIKE_PATTERN` 早就用這個特徵把它們擋在 IPC 分布外，
本模組與該處**同口徑**，不是新規則。

## 三分法

    設計 ＝ document_kind = 'S'
    新型 ＝ patent_type   = 'U'
    發明 ＝ patent_type   = 'P' AND document_kind <> 'S'

實測：設計 11＋新型 27＋發明 17 ＝ 55 ✅
`kind ∈ {A,A1,B,B1,B2}` ↔ `type='P'`、`kind='U'` ↔ `type='U'`，55 件無例外。

## 為什麼要有唯一定義處

設計案的判定會被**三個地方**用到：分群排除條件、報表的專利種類維度、母體說明。
⚠ 本專案已四次因「同一份知識兩處落點」靜默失敗——判定散開後，改了一處另一處
不會報錯，只會兩邊數字不一樣。故此處集中定義，其餘一律呼叫。
"""
from __future__ import annotations

from typing import Any

KIND_DESIGN = "設計"
KIND_UTILITY = "新型"
KIND_INVENTION = "發明"
KIND_UNKNOWN = "未標示"

# 設計案在 WIPS `文献种类` 的值。
DESIGN_DOCUMENT_KIND = "S"
# `发明专利/实用新型` 的兩個值。⚠ P 不等於發明——設計案也是 P，見模組說明。
TYPE_INVENTION = "P"
TYPE_UTILITY = "U"


def _clean(value: Any) -> str:
    """取乾淨字串；None／空白一律視為缺值。"""
    return str(value).strip() if value is not None else ""


def is_design(row: dict[str, Any]) -> bool:
    """這件是不是外觀設計。**判定設計案的唯一入口**。

    ⚠ 只看 `document_kind`。呼叫端不得自行寫 `document_kind == 'S'`——
    散開後改一處另一處不會報錯（見模組說明）。
    """
    return _clean(row.get("document_kind")).upper() == DESIGN_DOCUMENT_KIND


def patent_kind(row: dict[str, Any]) -> str:
    """回傳「發明」「新型」「設計」或「未標示」。

    ⚠ 兩欄皆空回「未標示」，**不得預設成發明**——那會把缺值灌進發明件數，
    讀者無從分辨「真的是發明」與「沒標」。
    """
    if is_design(row):
        return KIND_DESIGN
    ptype = _clean(row.get("patent_type")).upper()
    if ptype == TYPE_UTILITY:
        return KIND_UTILITY
    if ptype == TYPE_INVENTION:
        return KIND_INVENTION
    return KIND_UNKNOWN


def kind_tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    """逐列統計三類件數（含「未標示」，只在真的有缺值時才出現）。"""
    tally: dict[str, int] = {}
    for row in rows:
        tally[patent_kind(row)] = tally.get(patent_kind(row), 0) + 1
    return tally


def design_exclusion_note(rows: list[dict[str, Any]]) -> str:
    """設計案的母體說明。沒有設計案時回空字串。

    ⚠ **要同時交代排除誰與為什麼**：只寫「設計 11 件」讀者仍不知道為何被排除。
    ⚠ 沒有設計案時不得硬印一句——那會讓讀者以為有東西被排除了。
    """
    count = sum(1 for row in rows if is_design(row))
    if not count:
        return ""
    return f"設計 {count} 件無技術請求項，不列入主題分類"


def kind_summary(rows: list[dict[str, Any]]) -> str:
    """封面／母體說明用的一句話：總量、分析母體、設計案三個數字講完。

    分析母體＝總量 − 設計案：設計專利法律上沒有技術請求項，兩個分群通道
    本來就收不到它們（無獨立項、無效果摘要），此處只是把既成事實寫出來。
    """
    total = len(rows)
    designs = sum(1 for row in rows if is_design(row))
    functional = total - designs
    if not designs:
        return f"總量 {total} 件"
    return (f"總量 {total} 件；技術與功效分析 {functional} 件；"
            f"設計 {designs} 件只計件數不分類")
