from __future__ import annotations

from datetime import date, datetime
from typing import Any


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    digits = "".join(ch for ch in text if ch.isdigit())
    candidates = []
    if len(digits) == 8:
        candidates.append("%Y%m%d")
    if len(digits) == 6:
        candidates.append("%Y%m")

    candidates.extend(["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"])
    for fmt in candidates:
        try:
            parsed = datetime.strptime(digits if fmt in {"%Y%m%d", "%Y%m"} else text, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def year_from_date(value: date | None) -> int | None:
    return value.year if value else None
