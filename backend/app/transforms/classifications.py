from __future__ import annotations

import re
from typing import Any

from backend.app.transforms.text import clean_text

CLASSIFICATION_SPLIT_RE = re.compile(r"\s*(?:;|；|\||,|，|\n)+\s*")


def split_classifications(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part for part in CLASSIFICATION_SPLIT_RE.split(text) if part]
