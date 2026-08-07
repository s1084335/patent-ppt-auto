"""補分候選判定（CLU-013，openspec change add-technical-channel-ai-backfill）。

候選＝該通道無 embeddings（分群來源欄無值、不在分群輸入母體）∧ 非設計案。
本模組是**唯一定義處**：技術、功效與日後任何通道共用，呼叫端不得另寫條件。

⚠ 設計案判定一律走 transforms/patent_kind.is_design（該模組即判定的唯一
定義處）——外觀設計法律上沒有技術請求項，餵補分只會讓 AI 依用途詞硬猜
（2026-08-05 定案兩通道皆排除皆不補）。
"""
from __future__ import annotations

from typing import Any

from backend.app.transforms.patent_kind import is_design


def backfill_candidates(
    rows: list[dict[str, Any]],
    assigned_patent_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """從通道母體列出補分候選。

    rows 每列需含 `patent_id`、`source_text`（該通道分群來源欄的值）與
    `document_kind`；`source_text` 為 None／空白＝不在分群母體。
    assigned_patent_ids＝已有該通道 current assignment 者（含已核准的補分件），
    不再列候選——重跑語意由此保證冪等。
    """
    assigned = assigned_patent_ids or set()
    out: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("source_text") or "").strip()
        if text:
            continue
        if is_design(row):
            continue
        if int(row["patent_id"]) in assigned:
            continue
        out.append(row)
    return out
