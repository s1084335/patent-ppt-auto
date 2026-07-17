"""專利號原值與下游轉換值的共用規則。"""

from __future__ import annotations

import re
from typing import Any


UNEXAMINED_PUBLICATION_NUMBER_ORIGINAL = "未審查的公開號"
UNEXAMINED_PUBLICATION_NUMBER_TRANSFORMED = "未審查的公開號(轉換後)"
APPLICATION_NUMBER_ORIGINAL = "申請號"
APPLICATION_NUMBER_TRANSFORMED = "申請號(轉換後)"


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
