"""Workspace 唯讀查詢服務。

集中 workspace 清單／詳情的只讀 SQL，供 API 層呼叫。設計約束：
- 只讀不寫；沿用 get_pool() 借還連線與 dict_row，row_factory 設在 cursor 上避免污染池連線。
- patent_count 與 is_composed 以單一 SQL 內的相關子查詢求得，不對每筆 workspace 另發查詢
  （避免 N+1）；清單另發一次 COUNT 求 total（固定兩條查詢，非隨資料量成長的 N+1）。
- 詳情只回直接組合來源（workspace_compose_sources 一層），不遞迴展開、不回 patent 明細。
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from backend.app.db.connection import get_pool


# 單一 workspace 的共用投影欄位：list 與 detail 共用同一組欄位定義，確保兩端結構一致。
# patent_count／is_composed 用相關子查詢，投影在 LIMIT 之後計算，只對輸出列求值，不造成 N+1。
_WORKSPACE_FIELDS = """
    w.workspace_id,
    w.workspace_name,
    w.description,
    w.status,
    (
        SELECT count(*)
        FROM app_layer.workspace_patents wp
        WHERE wp.workspace_id = w.workspace_id
    ) AS patent_count,
    w.created_by,
    w.created_at,
    w.updated_at,
    EXISTS (
        SELECT 1
        FROM app_layer.workspace_compose_sources cs
        WHERE cs.workspace_id = w.workspace_id
    ) AS is_composed
"""


# 清單：固定排序 created_at DESC, workspace_id DESC；status 為 NULL 時不過濾。
# status 參數顯式轉 text，避免傳 NULL 時 PG 無法推斷型別（AmbiguousParameter）。
_LIST_SQL = f"""
SELECT {_WORKSPACE_FIELDS}
FROM app_layer.workspaces w
WHERE (%(status)s::text IS NULL OR w.status = %(status)s::text)
ORDER BY w.created_at DESC, w.workspace_id DESC
LIMIT %(limit)s OFFSET %(offset)s
"""


# total：套用與清單相同的 status filter。
_COUNT_SQL = """
SELECT count(*) AS total
FROM app_layer.workspaces w
WHERE (%(status)s::text IS NULL OR w.status = %(status)s::text)
"""


# 詳情：同投影欄位，鎖定單一 workspace_id。
_DETAIL_SQL = f"""
SELECT {_WORKSPACE_FIELDS}
FROM app_layer.workspaces w
WHERE w.workspace_id = %(workspace_id)s
"""


# 直接組合來源：join 回 workspaces 取來源的名稱與狀態；只回一層、依來源 id 排序。
_COMPOSE_SOURCES_SQL = """
SELECT
    cs.source_workspace_id,
    sw.workspace_name,
    sw.status,
    cs.source_patent_count,
    cs.created_at
FROM app_layer.workspace_compose_sources cs
JOIN app_layer.workspaces sw ON sw.workspace_id = cs.source_workspace_id
WHERE cs.workspace_id = %(workspace_id)s
ORDER BY cs.source_workspace_id
"""


# workspace 專利成員：只回前端選取所需的既有欄位（沿用 workspace_dashboard 的專利號
# COALESCE 與 title/country_code，不新增資料庫欄位）。額外欄位皆來自既有資料：
#   applicant_display_name：derived_layer.report_patent_base 既有正規化申請人顯示名（唯讀 LEFT JOIN，
#       report_patent_base 未涵蓋的專利回 NULL）。
#   has_technical_text／has_effect_text：技術（wips_independent_claims）與功效（effect_summary）
#       兩通道來源文本是否非空；欄名鏡射 clustering.sources 的 SOURCE_SPECS.source_column。
# CTE 先算出這些欄位，供外層以 keyword 對 title／patent_number／applicant_display_name 做 ILIKE
# 過濾；members 與 count 共用同一 CTE。
_WS_PATENTS_CTE = """
WITH ws_patents AS (
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
        rpb.applicant_display_name AS applicant_display_name,
        (NULLIF(BTRIM(p."獨立項[KR,JP,US,CN,EP,IN]"), '') IS NOT NULL) AS has_technical_text,
        (NULLIF(BTRIM(p."效果 摘要[US,EP,PCT,JP,KR,CN,TW]"), '') IS NOT NULL) AS has_effect_text
    FROM app_layer.workspace_patents wp
    JOIN core_layer.patents p ON p.id = wp.patent_id
    LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
    WHERE wp.workspace_id = %(workspace_id)s
)
"""

# keyword 為 NULL 時不過濾；否則對 title／patent_number／applicant_display_name 做 ILIKE
# （傳入值已包 %）。
_WS_PATENTS_WHERE = (
    "WHERE (%(kw)s::text IS NULL "
    "OR title ILIKE %(kw)s "
    "OR patent_number ILIKE %(kw)s "
    "OR applicant_display_name ILIKE %(kw)s)"
)

_WS_PATENTS_ITEMS_SQL = f"""
{_WS_PATENTS_CTE}
SELECT patent_id, patent_number, title, country_code,
       applicant_display_name, has_technical_text, has_effect_text
FROM ws_patents
{_WS_PATENTS_WHERE}
ORDER BY patent_id
LIMIT %(limit)s OFFSET %(offset)s
"""

_WS_PATENTS_COUNT_SQL = f"""
{_WS_PATENTS_CTE}
SELECT count(*) AS total
FROM ws_patents
{_WS_PATENTS_WHERE}
"""


def list_workspaces(
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
) -> dict[str, Any]:
    """分頁列出 workspace，含 patent_count 與 is_composed。

    回傳 {items, total, limit, offset}。排序固定 created_at DESC, workspace_id DESC；
    status 為 None 時不過濾，total 套用與 items 相同的 status filter。參數合法性由呼叫端
    （API 層）負責，本函式假設 limit/offset/status 已驗證。
    """
    params = {"status": status, "limit": limit, "offset": offset}
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_LIST_SQL, params)
            items = cur.fetchall()
            cur.execute(_COUNT_SQL, {"status": status})
            total = int(cur.fetchone()["total"])
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_workspace_detail(workspace_id: int) -> dict[str, Any] | None:
    """取單一 workspace 詳情，含直接組合來源（compose_sources）。

    workspace 不存在時回 None（由 API 層轉 404）。一般（非組合）workspace 的
    compose_sources 為空陣列。只回一層直接來源，不遞迴、不含 patent 明細。
    """
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_DETAIL_SQL, {"workspace_id": workspace_id})
            detail = cur.fetchone()
            if detail is None:
                return None
            cur.execute(_COMPOSE_SOURCES_SQL, {"workspace_id": workspace_id})
            detail["compose_sources"] = cur.fetchall()
    return detail


def list_workspace_patents(
    *,
    workspace_id: int,
    limit: int = 50,
    offset: int = 0,
    keyword: str | None = None,
) -> dict[str, Any] | None:
    """分頁列出 workspace 內專利成員，可選 keyword 對 title／patent_number／applicant_display_name 搜尋。

    每筆含既有欄位 patent_id／patent_number／title／country_code／applicant_display_name
    與完整度旗標 has_technical_text／has_effect_text。回傳 {items, total, limit, offset}；
    workspace 不存在時回 None（由 API 層轉 404）。keyword 去空白後為空視為不過濾。
    只回既有欄位，不含分群/topic 明細。
    """
    # keyword 去空白；有值才包成 ILIKE pattern，空字串或全空白視為不過濾。
    cleaned = keyword.strip() if keyword else None
    kw = f"%{cleaned}%" if cleaned else None
    params = {"workspace_id": workspace_id, "limit": limit, "offset": offset, "kw": kw}
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 先確認 workspace 存在，才能區分「不存在（404）」與「存在但無符合成員（空清單）」。
            cur.execute(
                "SELECT 1 FROM app_layer.workspaces WHERE workspace_id = %(workspace_id)s",
                {"workspace_id": workspace_id},
            )
            if cur.fetchone() is None:
                return None
            cur.execute(_WS_PATENTS_COUNT_SQL, params)
            total = int(cur.fetchone()["total"])
            cur.execute(_WS_PATENTS_ITEMS_SQL, params)
            items = cur.fetchall()
    return {"items": items, "total": total, "limit": limit, "offset": offset}
