"""legal_status（WIPS 状态欄）正規化。

WIPS 的状态值是跨局詞彙，實際值為簡體中文＋可選括號註記，例如：
    到期(Non-payment of Renewal / Annual fee)、放弃、撤回、授权、审查中
正規化流程：strip → 去尾端括號註記（半形/全形）→ casefold 查表。
查不到的值回傳 "unknown"，由呼叫端計數現形，不可無聲吞掉。

初版對照表依 DB 內 525 筆與 407/850 樣本檔的 distinct 值建立；
英文別名為備援（WIPS 英文介面匯出時欄名相同、值語言未驗，先收著）。
等力山 525 完整欄位重匯後，掃 DB distinct 值補全（見計畫 Phase 6）。
"""
from __future__ import annotations

import re

# 四種正規化結果：
#   alive   = 現有保護（貢獻國家佈局）
#   dead    = 已失效（不貢獻）
#   pending = 尚未取得保護（審中/公開，不貢獻、另計數）
#   unknown = 對照表沒有的值（不貢獻、必須計數現形）
STATUS_ALIVE = "alive"
STATUS_DEAD = "dead"
STATUS_PENDING = "pending"
STATUS_UNKNOWN = "unknown"

# key 一律小寫（casefold 後比對）；中文鍵以簡體為主（WIPS 原始值），繁體同收避免來源轉碼差異。
LEGAL_STATUS_NORMALIZATION: dict[str, str] = {
    # 簡體中文（DB/樣本實際觀測值）
    "授权": STATUS_ALIVE,
    "审查中": STATUS_PENDING,
    "申请": STATUS_PENDING,
    "公开": STATUS_PENDING,
    # 2026-08-07 全原始檔掃描補：授權公告尚未發生、權利未存續 → pending。
    # （主狀態欄 13 種 distinct 中唯一未收斂者，×7 件，見 decisions 同日紀錄。）
    "即将授权": STATUS_PENDING,
    "即將授權": STATUS_PENDING,
    "到期": STATUS_DEAD,
    "放弃": STATUS_DEAD,
    "撤回": STATUS_DEAD,
    "拒绝": STATUS_DEAD,
    "删除": STATUS_DEAD,
    "无效": STATUS_DEAD,
    # 繁體別名
    "授權": STATUS_ALIVE,
    "已核准": STATUS_ALIVE,
    "審查中": STATUS_PENDING,
    "申請": STATUS_PENDING,
    "已申請": STATUS_PENDING,
    "公開": STATUS_PENDING,
    "已公開": STATUS_PENDING,
    "放棄": STATUS_DEAD,
    "核駁": STATUS_DEAD,
    "拒絕": STATUS_DEAD,
    "刪除": STATUS_DEAD,
    "無效": STATUS_DEAD,
    "已失效": STATUS_DEAD,
    "屆滿失效": STATUS_DEAD,
    # 英文別名（備援）
    "registered": STATUS_ALIVE,
    "granted": STATUS_ALIVE,
    "grant": STATUS_ALIVE,
    "active": STATUS_ALIVE,
    "pending": STATUS_PENDING,
    "published": STATUS_PENDING,
    "application": STATUS_PENDING,
    "under examination": STATUS_PENDING,
    "expired": STATUS_DEAD,
    "withdrawn": STATUS_DEAD,
    "abandoned": STATUS_DEAD,
    "lapsed": STATUS_DEAD,
    "rejected": STATUS_DEAD,
    "revoked": STATUS_DEAD,
    "ceased": STATUS_DEAD,
    "deleted": STATUS_DEAD,
}

# 尾端括號註記：如 "到期(Expiration of the term)"、"到期（...）"。
# 只剝最外層一組尾端括號，括號內容任意（可含巢狀半形括號）。
_TRAILING_PAREN_RE = re.compile(r"\s*[(（].*[)）]\s*$")


def normalize_legal_status(raw: str | None) -> str:
    """把 WIPS 状态原始值正規化為 alive/dead/pending/unknown。"""
    if raw is None:
        return STATUS_UNKNOWN
    text = raw.strip()
    if not text:
        return STATUS_UNKNOWN
    # 去尾端括號註記後再查表；先查完整值，讓未來可能出現的
    # 「括號語意會翻轉判定」的值有機會被精確鍵覆蓋。
    key = text.casefold()
    if key in LEGAL_STATUS_NORMALIZATION:
        return LEGAL_STATUS_NORMALIZATION[key]
    stripped = _TRAILING_PAREN_RE.sub("", text).strip()
    key = stripped.casefold()
    return LEGAL_STATUS_NORMALIZATION.get(key, STATUS_UNKNOWN)


# TW 人工登錄值域：後端是唯一來源，前端只能透過 API 讀取 allowed_statuses。
# 🔴 2026-08-07 使用者定案：「已核准」→「授權」——WIPS 授權公告標「授權」，
# 用詞對齊；舊值「已核准」在 LEGAL_STATUS_NORMALIZATION 保留容忍（歷史資料
# 與 TW 匯出檔「專利狀態」欄都用它），只是不再是人工登錄選項。
TW_LEGAL_STATUS_ALLOWED: tuple[str, ...] = (
    "已申請",
    "已公開",
    "審查中",
    "授權",
    "放棄",
    "核駁",
    "撤回",
    "已失效",
    "屆滿失效",
)

TW_LEGAL_STATUS_ANALYSIS_MAP: dict[str, str] = {
    status: normalize_legal_status(status) for status in TW_LEGAL_STATUS_ALLOWED
}


def validate_tw_legal_status(status: str) -> str:
    """檢查 TW 人工登錄狀態並回傳去除前後空白的合法值。"""
    cleaned = (status or "").strip()
    if cleaned not in TW_LEGAL_STATUS_ALLOWED:
        raise ValueError(f"unsupported TW legal_status: {status}")
    return cleaned


def normalize_tw_legal_status_for_analysis(raw: str | None) -> str:
    """將 TW 人工狀態映射到狀態分析四分類；未知或空白一律 unknown。"""
    cleaned = (raw or "").strip()
    if not cleaned:
        return STATUS_UNKNOWN
    if cleaned not in TW_LEGAL_STATUS_ALLOWED:
        return STATUS_UNKNOWN
    return normalize_legal_status(cleaned)


# ── 顯示字面（2026-08-07 使用者定案：前端不得出現簡體）────────────────────
# WIPS 原值是簡體；顯示層轉繁體**只在這裡定義一次**，API 帶 display 欄、
# 前端只消費（一方產生、一方消費——前端自建對照就是第二份會漂移的知識）。
# ⚠ 只轉「本體詞」：到期的括號說明（英文）原樣保留；沒見過的值原樣回傳，
# 不得靜默改寫——顯示層不做語意判斷，語意收斂歸 normalize_legal_status。
_DISPLAY_TRADITIONAL: dict[str, str] = {
    "授权": "授權",
    "审查中": "審查中",
    "申请": "申請",
    "公开": "公開",
    "放弃": "放棄",
    "无效": "無效",
    "拒绝": "拒絕",
    "删除": "刪除",
    "即将授权": "即將授權",
}


def display_legal_status(raw: str | None) -> str | None:
    """回傳 legal_status 的繁體顯示字面；None 原樣回傳（前端顯示空白）。"""
    if raw is None:
        return None
    text = raw.strip()
    return _DISPLAY_TRADITIONAL.get(text, raw)
