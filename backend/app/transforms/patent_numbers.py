"""專利號原值與下游轉換值的共用規則。"""

from __future__ import annotations

import re
from typing import Any


UNEXAMINED_PUBLICATION_NUMBER_ORIGINAL = "未審查的公開號"
UNEXAMINED_PUBLICATION_NUMBER_TRANSFORMED = "未審查的公開號(轉換後)"
APPLICATION_NUMBER_ORIGINAL = "申請號"
APPLICATION_NUMBER_TRANSFORMED = "申請號(轉換後)"

# 🔴 顯示用專利號的挑選順序——**唯一定義處**（2026-08-04 治本）。
# 曾被複製四份（patent_queries／workspace_queries／clustering.runner／
# clustering.workspace_service），第五個消費端 cluster_data_loader 漏抄成
# 「只取原值公開號」：TW 案代表專利顯示西元前綴（202421229 而非 11321229）、
# M 開頭授權案（公開號 NULL）直接空白。副本各自演進不會報錯，症狀只在
# 報表上看得到——此後鏈只在這裡定義，消費端一律 import display_number_sql。
# 順序語意：公告號（授權>審查）優先於公開號；同一種號**轉換後優先於原值**
# （TW 扣 1911 的機制就靠這個順位生效，反了機制就白做）。
DISPLAY_NUMBER_PRIORITY = (
    "授權公告號",
    "審查的公告號",
    UNEXAMINED_PUBLICATION_NUMBER_TRANSFORMED,
    UNEXAMINED_PUBLICATION_NUMBER_ORIGINAL,
    APPLICATION_NUMBER_TRANSFORMED,
    APPLICATION_NUMBER_ORIGINAL,
)


def display_number_sql(alias: str = "p") -> str:
    """產生顯示用專利號的 COALESCE SQL 片段（欄位序見 DISPLAY_NUMBER_PRIORITY）。"""
    parts = ",\n            ".join(
        f"NULLIF(BTRIM({alias}.\"{column}\"), '')" for column in DISPLAY_NUMBER_PRIORITY
    )
    return f"COALESCE(\n            {parts}\n        )"


def transform_patent_number(country_code: Any, patent_number: Any) -> str | None:
    """非 TW 保留原值；TW 的四位西元年前綴轉成三位民國年。"""
    raw_number = _clean_value(patent_number)
    if raw_number is None:
        return None
    normalized_country = (_clean_value(country_code) or "").upper()
    if normalized_country != "TW":
        return raw_number

    # 只轉最前方四位有效西元年，後面的數字與分隔符完全保留。
    match = re.match(r"^(\d{4})(.*)$", raw_number)
    if match is None:
        return raw_number
    western_year = int(match.group(1))
    if not 1912 <= western_year <= 2910:
        return raw_number
    return f"{western_year - 1911:03d}{match.group(2)}"


def transformed_number_fields(number_fields: dict[str, Any]) -> dict[str, str | None]:
    """依 country_code 產生兩個可直接寫入核心資料物件的轉換後欄位。"""
    country_code = number_fields.get("country_code")
    return {
        UNEXAMINED_PUBLICATION_NUMBER_TRANSFORMED: transform_patent_number(
            country_code,
            number_fields.get(UNEXAMINED_PUBLICATION_NUMBER_ORIGINAL),
        ),
        APPLICATION_NUMBER_TRANSFORMED: transform_patent_number(
            country_code,
            number_fields.get(APPLICATION_NUMBER_ORIGINAL),
        ),
    }


def _clean_value(value: Any) -> str | None:
    """只去除外圍空白與空值，不改專利號內部格式。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text
