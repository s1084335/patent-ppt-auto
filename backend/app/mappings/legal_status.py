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
    "到期": STATUS_DEAD,
    "放弃": STATUS_DEAD,
    "撤回": STATUS_DEAD,
    "拒绝": STATUS_DEAD,
    "删除": STATUS_DEAD,
    "无效": STATUS_DEAD,
    # 繁體別名
    "授權": STATUS_ALIVE,
    "審查中": STATUS_PENDING,
    "申請": STATUS_PENDING,
    "公開": STATUS_PENDING,
    "放棄": STATUS_DEAD,
    "拒絕": STATUS_DEAD,
    "刪除": STATUS_DEAD,
    "無效": STATUS_DEAD,
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
