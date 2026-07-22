"""既有專利庫的唯讀查詢服務（供案件比對選取被比對專利）。

集中專利號搜尋的只讀 SQL。設計約束：
- 只讀不寫；沿用 get_pool() 借還連線與 dict_row，row_factory 設在 cursor 上避免污染池連線。
- 專利號機制沿用 workspace_queries 的六欄 COALESCE（授權公告號 / 審查的公告號 /
  未審查的公開號(轉換後) / 未審查的公開號 / 申請號(轉換後) / 申請號），不綁單一號格式或欄位。
- 單一 SQL 批次查 + LIMIT 上限，不逐筆查、不全表掃（limit 上限由 API 層以 le=200 擋）。
- applicant_display_name 由 derived_layer.report_patent_base LEFT JOIN 取（未涵蓋者回 NULL）。
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from backend.app.db.connection import get_pool


# 六欄專利號 COALESCE（與 workspace_queries 的 ws_patents CTE 同順序，唯一事實共用同一規則）。
# 以 CTE 先算出 patent_number 與 applicant，供外層對 patent_number ILIKE 過濾。
_PATENT_SEARCH_SQL = """
WITH candidates AS (
    SELECT
        p.id AS patent_id,
        COALESCE(
            NULLIF(BTRIM(p."授權公告號"), ''),
            NULLIF(BTRIM(p."審查的公告號"), ''),
            NULLIF(BTRIM(p."未審查的公開號(轉換後)"), ''),
            NULLIF(BTRIM(p."未審查的公開號"), ''),
            NULLIF(BTRIM(p."申請號(轉換後)"), ''),
            NULLIF(BTRIM(p."申請號"), '')
        ) AS patent_number,
        p.title,
        p.country_code,
        rpb.applicant_display_name AS applicant_display_name
    FROM core_layer.patents p
    LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
)
SELECT patent_id, patent_number, title, country_code, applicant_display_name
FROM candidates
WHERE patent_number ILIKE %(q)s
ORDER BY patent_id
LIMIT %(limit)s
"""


def search_patents(*, q: str, limit: int = 20) -> dict[str, Any]:
    """以專利號（六欄 COALESCE）ILIKE 搜尋既有庫專利，回 {items}。

    q 去空白後為空回空清單（不發查詢，避免 '%%' 命中全庫）。ILIKE pattern 對 q 做前後包 %，
    支援精確號與片段命中。limit 上限與型別由 API 層負責（le=200），本函式假設已驗證。
    回傳每筆含 patent_id/patent_number/title/country_code/applicant_display_name；
    無 patent_number 的專利不會被號搜命中（COALESCE 為 NULL，ILIKE 不成立）。
    """
    cleaned = (q or "").strip()
    if not cleaned:
        return {"items": []}
    pattern = f"%{cleaned}%"
    params = {"q": pattern, "limit": limit}
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_PATENT_SEARCH_SQL, params)
            items = cur.fetchall()
    return {"items": items}
