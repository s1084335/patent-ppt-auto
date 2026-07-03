from __future__ import annotations

import re
from typing import Any

from backend.app.transforms.text import clean_text

PERSON_SPLIT_RE = re.compile(r"\s*(?:;|；|\||\n|、|，)+\s*")


def split_people(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part for part in PERSON_SPLIT_RE.split(text) if part]


def merge_person_columns(columns: dict[str, list[str | None]]) -> list[dict[str, str | int | None]]:
    max_len = max((len(values) for values in columns.values()), default=0)
    people = []
    for index in range(max_len):
        row: dict[str, str | int | None] = {"sequence": index + 1}
        has_value = False
        for key, values in columns.items():
            value = values[index] if index < len(values) else None
            row[key] = value
            has_value = has_value or bool(value)
        if has_value:
            people.append(row)
    return people
