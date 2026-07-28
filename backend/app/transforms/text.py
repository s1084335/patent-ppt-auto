from __future__ import annotations

from typing import Any


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return " ".join(text.split())


def clean_long_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


def value_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


# WIPS 匯出以 ` | ` 分隔同一欄的多個值（多申請人／多專利權人／多受讓人）。
# 實測 60 筆庫內：申請人 14 筆、最近專利權人 10 筆、最近受讓人 1 筆含此分隔符。
MULTI_VALUE_SEPARATOR = "|"


def split_multi_value(value: Any) -> list[str]:
    """拆開 WIPS 的多值欄位，回傳去空白、去空段的名稱清單。

    ⚠ 不拆的後果（2026-07-28 使用者實機發現）：整串被當成一個公司名，
    「XIAMEN DMASTER HEALTH TECH Co.,Ltd. | Zeng Qing」會進待補清單變成
    查不到代碼的假公司；同一家公司也會因共同申請人不同而散成多筆收斂不起來。
    """
    text = clean_text(value)
    if not text:
        return []
    return [part for part in (p.strip() for p in text.split(MULTI_VALUE_SEPARATOR)) if part]


def primary_value(value: Any) -> str | None:
    """取主申請人＝多值欄位的第一個（WIPS 慣例第一個是主申請人）。

    2026-07-28 使用者定案：顯示只取主申請人，其餘不進顯示名也不計統計。
    """
    parts = split_multi_value(value)
    return parts[0] if parts else None
