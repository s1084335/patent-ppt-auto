"""既有專利庫的唯讀查詢服務（供案件比對選取被比對專利、專利總覽跨 workspace 顯示）。

集中專利號搜尋與全庫分頁清單的只讀 SQL。設計約束：
- 只讀不寫；沿用 get_pool() 借還連線與 dict_row，row_factory 設在 cursor 上避免污染池連線。
- 專利號機制沿用 workspace_queries 的六欄 COALESCE（授權公告號 / 審查的公告號 /
  未審查的公開號(轉換後) / 未審查的公開號 / 申請號(轉換後) / 申請號），不綁單一號格式或欄位。
- 單一 SQL 批次查 + LIMIT 上限，不逐筆查、不全表掃（limit 上限由 API 層以 le=200 擋）。
- applicant_display_name 由 derived_layer.report_patent_base LEFT JOIN 取（未涵蓋者回 NULL）。
- workspace 歸屬（list_patents）以「本頁 patent_id 一次批次反查」求得，不對每筆專利另發查詢
  （避免 N+1）；查詢次數固定三條（count / items / membership），不隨資料量成長。
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from backend.app.db.connection import get_pool


# 六欄專利號 COALESCE（與 workspace_queries 的 ws_patents CTE 同順序，唯一事實共用同一規則）。
# 以 CTE 先算出 patent_number 與 applicant，供外層對 patent_number／title 過濾。
# 號搜（search_patents）與全庫清單（list_patents）共用此 CTE，不重寫兩份專利號規則。
_CANDIDATES_CTE = """
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
"""

_PATENT_SEARCH_SQL = f"""
{_CANDIDATES_CTE}
SELECT patent_id, patent_number, title, country_code, applicant_display_name
FROM candidates
WHERE patent_number ILIKE %(q)s
ORDER BY patent_id
LIMIT %(limit)s
"""


# 全庫清單過濾：keyword 為 NULL 時不過濾；否則對 patent_number／title／applicant_display_name
# 做 ILIKE（傳入值已包 %），與 workspace 專利清單同一組可搜欄位。
_LIST_WHERE = (
    "WHERE (%(kw)s::text IS NULL "
    "OR patent_number ILIKE %(kw)s "
    "OR title ILIKE %(kw)s "
    "OR applicant_display_name ILIKE %(kw)s)"
)

_PATENT_LIST_ITEMS_SQL = f"""
{_CANDIDATES_CTE}
SELECT patent_id, patent_number, title, country_code, applicant_display_name
FROM candidates
{_LIST_WHERE}
ORDER BY patent_id
LIMIT %(limit)s OFFSET %(offset)s
"""

_PATENT_LIST_COUNT_SQL = f"""
{_CANDIDATES_CTE}
SELECT count(*) AS total
FROM candidates
{_LIST_WHERE}
"""


# workspace 歸屬反查：以本頁的 patent_id 陣列一次批次查完（單一 SQL），不逐筆查。
# 成員來源＝app_layer.workspaces.patent_ids_json 陣列（0021 後的唯一成員來源），
# 以 jsonb_array_elements 展開後與傳入 pids 交集；一筆專利可對應多個 workspace。
_PATENT_WORKSPACES_SQL = """
SELECT (m.pid)::bigint AS patent_id, w.workspace_id, w.workspace_name
FROM app_layer.workspaces w
JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
WHERE (m.pid)::bigint = ANY(%(pids)s)
ORDER BY (m.pid)::bigint, w.workspace_id
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


def list_patents(
    *, limit: int = 50, offset: int = 0, keyword: str | None = None
) -> dict[str, Any]:
    """分頁列出全庫專利（不分 workspace），每筆標示所屬 workspace。

    供專利總覽跨 workspace 顯示：資料一律分頁（limit 上限由 API 層以 le=200 擋），
    不一次撈全庫。keyword 去空白後為空視為不過濾，有值時對 patent_number／title／
    applicant_display_name 做 ILIKE。

    回 {items, total, limit, offset}；每筆含 patent_id／patent_number／title／
    country_code／applicant_display_name／workspaces（[{workspace_id, workspace_name}]，
    不屬任何 workspace 者為空陣列）。

    效率：固定三條查詢（count / items / 本頁 patent_id 批次反查 workspace 歸屬），
    不對每筆專利另發查詢（無 N+1）；歸屬在 Python 層以 dict 合併回清單。
    """
    cleaned = keyword.strip() if keyword else None
    kw = f"%{cleaned}%" if cleaned else None
    params = {"kw": kw, "limit": limit, "offset": offset}
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_PATENT_LIST_COUNT_SQL, {"kw": kw})
            total = int(cur.fetchone()["total"])
            cur.execute(_PATENT_LIST_ITEMS_SQL, params)
            items = cur.fetchall()
            # 本頁 patent_id 一次批次反查歸屬；空頁時仍發同一條查詢（帶空陣列），
            # 讓查詢次數與筆數無關（N+1 防護測試以次數相同為契約）。
            pids = [it["patent_id"] for it in items]
            cur.execute(_PATENT_WORKSPACES_SQL, {"pids": pids})
            membership_rows = cur.fetchall()
    membership: dict[int, list[dict[str, Any]]] = {}
    for row in membership_rows:
        membership.setdefault(row["patent_id"], []).append(
            {"workspace_id": row["workspace_id"], "workspace_name": row["workspace_name"]}
        )
    for it in items:
        it["workspaces"] = membership.get(it["patent_id"], [])
    return {"items": items, "total": total, "limit": limit, "offset": offset}
