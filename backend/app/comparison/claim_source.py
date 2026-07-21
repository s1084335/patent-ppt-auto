"""案件比對 · Claim 文字抽取（自 core_layer.patents 取權利要求文字）。

欄名查證（information_schema，patent_ppt，2026-07-21 實查）：
- 主來源欄「所有權利要求」實際欄名為 `所有權利要求[JP,KR,CN]`（帶括號後綴，非純字面）。
- 後備獨立項欄實際欄名為 `獨立項[KR,JP,US,CN,EP,IN]`。
- `主權項`、`主權項(原文)` 存在但一律不作為 Claim 文字來源（定案禁用）。
- **無「從屬項」文字欄**：僅有 `獨立項數量[KR,JP,US,CN,EP,IN]`（計數）與獨立項文字；
  故定案「獨立項＋從屬項」後備在現有 schema 只能取獨立項文字，從屬項文字待匯入來源補齊
  （design doc §8.1 開放問題）。此處後備只回獨立項並標記，不用主權項假裝完整。

抽取規則（定案）：優先「所有權利要求」欄；該欄空值才後備獨立項；皆空回明確錯誤。
回傳的 source_fields 使用抽象來源標記（`所有權利要求`／`獨立項`），對應 claim_model 白名單，
不外洩 DB 欄位字面（欄名可能隨匯入來源變動，抽象標記較穩定）。
"""
from __future__ import annotations

from typing import Any

# 實查欄名（見模組 docstring），SQL 內以雙引號包裹
COL_ALL_CLAIMS = "所有權利要求[JP,KR,CN]"
COL_INDEPENDENT = "獨立項[KR,JP,US,CN,EP,IN]"

# 對應 claim_model.ALLOWED_SOURCE_FIELDS 的抽象來源標記
SOURCE_ALL_CLAIMS = "所有權利要求"
SOURCE_INDEPENDENT = "獨立項"


class ClaimSourceError(ValueError):
    """Claim 文字抽取相關錯誤基底。"""


class ClaimSourceNotFoundError(ClaimSourceError):
    """指定 patent 不存在。"""


class ClaimSourceEmptyError(ClaimSourceError):
    """主欄與後備獨立項皆空，無可用 Claim 文字（不得回空字串當有效輸入）。"""


def _connect(connect_kwargs: dict[str, Any] | None):
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**(connect_kwargs or get_connection_kwargs()))


def _has_value(value: Any) -> bool:
    """欄位是否有實質內容（非 None、去空白後非空）。"""
    return isinstance(value, str) and value.strip() != ""


def extract_claim_source(patent_id: int, connect_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """抽取單一 patent 的 Claim 文字。

    回傳 {patent_id, text, source_fields}；source_fields 標明實際使用來源。
    Raises:
        ClaimSourceNotFoundError: patent 不存在。
        ClaimSourceEmptyError: 主欄與後備獨立項皆空。
    """
    with _connect(connect_kwargs) as conn:
        row = conn.execute(
            f'SELECT "{COL_ALL_CLAIMS}", "{COL_INDEPENDENT}" '
            "FROM core_layer.patents WHERE id = %s",
            (patent_id,),
        ).fetchone()
    if row is None:
        raise ClaimSourceNotFoundError(f"patent {patent_id} 不存在")
    all_claims, independent = row
    if _has_value(all_claims):
        return {"patent_id": patent_id, "text": all_claims.strip(),
                "source_fields": [SOURCE_ALL_CLAIMS]}
    if _has_value(independent):
        # 後備：現有 schema 無從屬項文字欄，只取獨立項並標記缺口
        return {"patent_id": patent_id, "text": independent.strip(),
                "source_fields": [SOURCE_INDEPENDENT]}
    raise ClaimSourceEmptyError(
        f"patent {patent_id} 無可用 Claim 文字（所有權利要求與獨立項皆空）")
