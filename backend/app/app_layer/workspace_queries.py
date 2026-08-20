"""Workspace 唯讀查詢服務。

集中 workspace 清單／詳情的只讀 SQL，供 API 層呼叫。設計約束：
- 只讀不寫；沿用 get_pool() 借還連線與 dict_row，row_factory 設在 cursor 上避免污染池連線。
- patent_count 與 is_composed 以單一 SQL 內的相關子查詢／表達式求得，不對每筆 workspace 另發
  查詢（避免 N+1）；清單另發一次 COUNT 求 total（固定兩條查詢，非隨資料量成長的 N+1）。
- 詳情只回直接組合來源（一層 compose sources），不遞迴展開、不回 patent 明細。

0021 對齊：app_layer.workspaces 只剩 workspace_id/workspace_name/status/patent_ids_json/
settings_json。成員專利＝patent_ids_json 陣列（0018 的 workspace_patents 已下沉
legacy_0021），patent_count＝陣列長度，is_composed＝該 ws 在 legacy_0021.workspace_compose_sources
有記錄；compose 明細與成員 join 皆改走 legacy_0021 / core_layer。0018 的
description/created_by/created_at/updated_at 欄在 0021 已無來源，不再投影（前端骨架依既有
形狀接：只是欄位來源改變，欄位名不變）。
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from backend.app.app_layer import patent_queries
from backend.app.db.connection import get_pool
from backend.app.transforms.patent_numbers import display_number_sql
from backend.app.repositories.topic_state_repository import (
    PostgresTopicStateRepository,
    TopicStateNotFoundError,
)

# 所屬主題預設通道：與前端分類區同用技術通道白名單值；可由呼叫端覆寫。
DEFAULT_TOPIC_SOURCE_FIELD = "wips_independent_claims"


# 單一 workspace 的共用投影欄位：list 與 detail 共用同一組欄位定義，確保兩端結構一致。
# 0021：patent_count＝patent_ids_json 陣列長度（成員即此陣列，非 join workspace_patents）；
# is_composed＝該 ws 在 legacy_0021.workspace_compose_sources 有記錄（相關子查詢，投影在
# LIMIT 之後求值，不造成 N+1）。description/created_by/created_at/updated_at 在 0021 無來源，不投影。
#   purpose：匯入批次用途標籤（2026-07-22），general／case_comparison，落 settings_json.purpose；
#       缺省（舊 workspace 無此鍵）視為 general，供專利總覽依用途過濾/顯示。
_WORKSPACE_FIELDS = """
    w.workspace_id,
    w.workspace_name,
    w.status,
    COALESCE(w.settings_json->>'purpose', 'general') AS purpose,
    jsonb_array_length(w.patent_ids_json) AS patent_count,
    EXISTS (
        SELECT 1
        FROM legacy_0021.workspace_compose_sources cs
        WHERE cs.workspace_id = w.workspace_id
    ) AS is_composed,
    w.is_global
"""


# 清單：固定排序 workspace_id DESC（0021 已無 created_at，改用穩定鍵 workspace_id）；
# status 為 NULL 時不過濾。status/purpose 參數顯式轉 text，避免傳 NULL 時 PG 無法推斷型別
# （AmbiguousParameter）。purpose 過濾以 COALESCE(...,'general') 對齊投影，讓舊 workspace（無
# purpose 鍵）在 purpose='general' 時被納入。
_PURPOSE_FILTER = (
    "AND (%(purpose)s::text IS NULL "
    "OR COALESCE(w.settings_json->>'purpose', 'general') = %(purpose)s::text)"
)

_LIST_SQL = f"""
SELECT {_WORKSPACE_FIELDS}
FROM app_layer.workspaces w
WHERE (%(status)s::text IS NULL OR w.status = %(status)s::text)
{_PURPOSE_FILTER}
ORDER BY w.workspace_id DESC
LIMIT %(limit)s OFFSET %(offset)s
"""


# total：套用與清單相同的 status＋purpose filter。
_COUNT_SQL = f"""
SELECT count(*) AS total
FROM app_layer.workspaces w
WHERE (%(status)s::text IS NULL OR w.status = %(status)s::text)
{_PURPOSE_FILTER}
"""


# 詳情：同投影欄位，鎖定單一 workspace_id。
_DETAIL_SQL = f"""
SELECT {_WORKSPACE_FIELDS}
FROM app_layer.workspaces w
WHERE w.workspace_id = %(workspace_id)s
"""


# 直接組合來源：0021 已把 compose lineage 下沉 legacy_0021.workspace_compose_sources
# （欄位 workspace_id/source_workspace_id/source_patent_count/created_at 皆存），join 回
# app_layer.workspaces 取來源名稱與狀態；只回一層、依來源 id 排序。
_COMPOSE_SOURCES_SQL = """
SELECT
    cs.source_workspace_id,
    sw.workspace_name,
    sw.status,
    cs.source_patent_count,
    cs.created_at
FROM legacy_0021.workspace_compose_sources cs
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
# 0021：成員來源改為 workspaces.patent_ids_json 陣列（0018 的 workspace_patents 已下沉 legacy_0021），
# 以 jsonb_array_elements 取出 patent_id 後 join core_layer.patents；其餘投影欄位不變。
# CTE 先算出這些欄位，供外層以 keyword 對 title／patent_number／applicant_display_name 做 ILIKE
# 過濾；members 與 count 共用同一 CTE。
# 2026-07-23 顯示欄位定案：分類區與專利總覽**共用同一組欄位**（使用者定案「不做兩套」），
# 故投影直接匯入 patent_queries.display_projection()，不在此重寫一份欄名清單。
# patent_people 為一對一（PK 為 patent_id），LEFT JOIN 不放大列數；patent_attributes
# 由 display_projection 產生的純量子查詢處理（一對多取最新非空），皆隨本 SQL 一次求值。
_WS_PATENTS_CTE = f"""
WITH ws_patents AS (
    SELECT
        p.id AS patent_id,
        {display_number_sql("p")} AS patent_number,
        -- 只回「有無代表圖」布林；bytea 不進清單，圖走 GET /patents/{{id}}/figure 惰性載入。
        (p."主附圖" IS NOT NULL) AS has_figure,
        rpb.applicant_display_name AS applicant_display_name,
        (NULLIF(BTRIM(p."獨立項[KR,JP,US,CN,EP,IN]"), '') IS NOT NULL) AS has_technical_text,
        (NULLIF(BTRIM(p."效果 摘要[US,EP,PCT,JP,KR,CN,TW]"), '') IS NOT NULL) AS has_effect_text,
        {patent_queries.display_projection()}
    FROM app_layer.workspaces w
    JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
    JOIN core_layer.patents p ON p.id = (m.pid)::bigint
    LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
    LEFT JOIN core_layer.patent_people pp ON pp.patent_id = p.id
    WHERE w.workspace_id = %(workspace_id)s
)
"""

# 成員清單投影欄位：由顯示欄位定義推導，不逐個寫死欄名。
_WS_PATENTS_SELECT_KEYS = ",\n       ".join(
    (
        "patent_id",
        "patent_number",
        "has_figure",
        "applicant_display_name",
        "has_technical_text",
        "has_effect_text",
        *patent_queries.display_field_keys(),
    )
)

# keyword 為 NULL 時不過濾；否則走 derived_layer.patent_search_terms，避免多值欄位漏查。
_WS_PATENTS_WHERE = (
    "WHERE (%(kw_lookup)s::text IS NULL "
    f"OR {patent_queries.search_terms_exists_sql('ws_patents')})"
)

_WS_PATENTS_ITEMS_SQL = f"""
{_WS_PATENTS_CTE}
SELECT {_WS_PATENTS_SELECT_KEYS}
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


# topic patents：沿用 ws_patents CTE（同一 workspace 成員投影），再以 patent_id = ANY 交集
# 指派到該 topic 的專利。欄位與 workspace 成員清單相同（共用同一份顯示欄位定義），
# 依 patent_id 升冪、可分頁。
_TOPIC_PATENTS_ITEMS_SQL = f"""
{_WS_PATENTS_CTE}
SELECT {_WS_PATENTS_SELECT_KEYS}
FROM ws_patents
WHERE patent_id = ANY(%(pids)s)
  AND (%(kw_lookup)s::text IS NULL OR {patent_queries.search_terms_exists_sql('ws_patents')})
ORDER BY patent_id
LIMIT %(limit)s OFFSET %(offset)s
"""

_TOPIC_PATENTS_COUNT_SQL = f"""
{_WS_PATENTS_CTE}
SELECT count(*) AS total
FROM ws_patents
WHERE patent_id = ANY(%(pids)s)
  AND (%(kw_lookup)s::text IS NULL OR {patent_queries.search_terms_exists_sql('ws_patents')})
"""


def assigned_patent_ids(*, workspace_id: int, source_field: str, topic_key: str) -> list[int]:
    """取指派到指定 topic 的專利 ID（唯一來源＝derived_layer.topic_assignments）。

    分群寫入端把指派關係寫進 topic_assignments 表（run_id, patent_id, topic_key），
    topic JSON 內**沒有** patent_ids 鍵——讀取端一律走本函式，不要自行從 JSON 找。

    ⚠ **必須跨 run 取**（2026-07-27 實機修）：incremental run **只寫新增專利的
    assignment**，舊專利的留在先前的 full/merge run。原本傳單一 run_id 只查那個 run，
    增量分群後「主題標籤顯示 26 筆、點進去卻是空的」——有些主題有、有些沒有，
    取決於它的專利多數落在哪個 run。

    做法：該 workspace/通道**全部 run** 中，每個 patent_id 取 run_id 最大的那筆
    （DISTINCT ON），再篩出屬於本 topic 者。專利在新 run 被改派到別的主題時，
    以最新 run 為準、不會兩邊都算。此規則與 topic_state_repository 的 assignments
    取法一致（該處 docstring 早已寫明），本函式原本沒跟上。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT patent_id FROM (
                    SELECT DISTINCT ON (ta.patent_id)
                           ta.patent_id, ta.topic_key
                    FROM derived_layer.topic_assignments ta
                    JOIN derived_layer.topic_runs tr ON tr.run_id = ta.run_id
                    JOIN app_layer.workflow_runs wr ON wr.run_id = tr.workflow_run_id
                    WHERE wr.workspace_id = %(workspace_id)s
                      AND tr.source_field = %(source_field)s
                    ORDER BY ta.patent_id, ta.run_id DESC
                ) latest
                WHERE latest.topic_key = %(topic_key)s
                ORDER BY patent_id
                """,
                {
                    "workspace_id": workspace_id,
                    "source_field": source_field,
                    "topic_key": topic_key,
                },
            )
            return [int(row[0]) for row in cur.fetchall()]


def _topic_assignment_map(
    workspace_id: int, source_field: str
) -> dict[int, tuple[str, str | None]]:
    """回 patent_id → (topic_code, label) 映射：走 topic_state_repository 的指派關係（非 label
    文字比對）。該 workspace/通道尚無分群時回空 dict（成員一律視為未分類）。

    以 Python 層 dict 合併回專利清單，不改成員查詢的 SQL，避免 workspace_queries 與 topic
    schema 硬耦合；source_field 可參數化，不寫死單一 workspace 或通道。
    """
    try:
        state = PostgresTopicStateRepository().get_latest_topic_state(workspace_id, source_field)
    except (TopicStateNotFoundError, ValueError):
        # 無分群 run 或非法通道：全部視為未分類，總覽照常顯示。
        return {}
    # ⚠ 指派關係的唯一來源是 derived_layer.topic_assignments，**不是** topic JSON。
    # 2026-07-27 前這裡讀 topic.get("patent_ids")，但寫入端（runner._persist_final_topics）
    # 從未產生該鍵（只有 representative_patent_ids），於是永遠取到空 dict——
    # 總覽的「技術分類／功效分類」欄因此一直是空白。
    label_by_code = {
        t.get("topic_code"): t.get("label")
        for t in state.get("topics", [])
        if t.get("topic_code")
    }
    run_id = state.get("run_id")
    if run_id is None:
        return {}
    # 一次取整個 run 的指派（非逐 topic 呼叫 assigned_patent_ids，避免 N+1）
    mapping: dict[int, tuple[str, str | None]] = {}
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT patent_id, topic_key FROM derived_layer.topic_assignments "
                "WHERE run_id = %(run_id)s",
                {"run_id": int(run_id)},
            )
            for patent_id, topic_key in cur.fetchall():
                mapping[int(patent_id)] = (topic_key, label_by_code.get(topic_key))
    return mapping


def list_workspaces(
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """分頁列出 workspace，含 purpose、patent_count 與 is_composed。

    回傳 {items, total, limit, offset}。排序固定 workspace_id DESC（0021 已無 created_at，
    改用穩定鍵 workspace_id）；status／purpose 為 None 時各自不過濾，total 套用與 items 相同的
    filter。purpose 過濾對齊投影的 COALESCE(...,'general')，讓舊 workspace 也能被 general 命中。
    參數合法性由呼叫端（API 層）負責，本函式假設 limit/offset/status/purpose 已驗證。
    """
    params = {"status": status, "purpose": purpose, "limit": limit, "offset": offset}
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_LIST_SQL, params)
            items = cur.fetchall()
            cur.execute(_COUNT_SQL, {"status": status, "purpose": purpose})
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
    source_field: str = DEFAULT_TOPIC_SOURCE_FIELD,
) -> dict[str, Any] | None:
    """分頁列出 workspace 內專利成員，可選 keyword 對 title／patent_number／applicant_display_name 搜尋。

    每筆含既有欄位 patent_id／patent_number／title／country_code／applicant_display_name、
    完整度旗標 has_technical_text／has_effect_text，以及所屬主題 topic_key／topic_label
    （該專利在最新分群（source_field 通道）的歸屬，未分類者為 None）。所屬主題走
    topic_state_repository 的指派關係，非 label 文字比對；無分群 workspace 全部為未分類。
    回傳 {items, total, limit, offset}；workspace 不存在時回 None（由 API 層轉 404）。
    keyword 去空白後為空視為不過濾。
    """
    # keyword 去空白；有值才包成 ILIKE pattern，空字串或全空白視為不過濾。
    kw_lookup = patent_queries.keyword_lookup_pattern(keyword)
    params = {"workspace_id": workspace_id, "limit": limit, "offset": offset, "kw_lookup": kw_lookup}
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
    _attach_topic_columns(items, workspace_id=workspace_id, source_field=source_field)
    # 推導型顯示欄位：與全庫清單同一份唯一定義處，前端只消費。
    patent_queries.attach_display_fields(items)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _attach_topic_columns(
    items: list[dict[str, Any]],
    *,
    workspace_id: int,
    source_field: str | None = None,
) -> None:
    """就地補上主題欄位：單一 topic_key/topic_label ＋ 各通道的 topic_*_<source_field>。

    ⚠ **列表類查詢一律走這裡**，不要各自實作——2026-07-27 踩到：分通道欄位只加在
    list_workspace_patents，list_topic_patents 沒加，導致「全部」有值、點進單一主題
    後技術分類／功效分類兩欄全空。同一份資料兩個查詢函式只改一個，是本專案反覆出現
    的斷鏈型態。

    前端「技術分類／功效分類」是兩個獨立欄，key 由 topicLabelKey(source_field)
    推導成 topic_label_<source_field>；單一 topic_label 只反映查詢參數的那一個通道，
    保留是為了不打壞其他呼叫端。source_field 為 None 時不設單一欄位（呼叫端沒有
    「當前通道」概念，例如主題專利清單本身已鎖定在某主題）。
    """
    from backend.app.clustering.sources import source_fields

    maps: dict[str, dict[int, tuple[str | None, str | None]]] = {}
    for field in source_fields():
        maps[field] = _topic_assignment_map(workspace_id, field)
    if source_field is not None:
        current = maps.get(source_field) or _topic_assignment_map(workspace_id, source_field)
        for it in items:
            code, label = current.get(it["patent_id"], (None, None))
            it["topic_key"] = code
            it["topic_label"] = label
    for field, field_map in maps.items():
        for it in items:
            code, label = field_map.get(it["patent_id"], (None, None))
            it[f"topic_key_{field}"] = code
            it[f"topic_label_{field}"] = label


def list_topic_patents(
    *,
    workspace_id: int,
    patent_ids: list[int],
    limit: int = 50,
    offset: int = 0,
    keyword: str | None = None,
) -> dict[str, Any]:
    """分頁列出指派到某 topic 的專利明細（供分類區點主題後列該主題專利）。

    patent_ids＝該 topic 在最新分群的指派專利（來源＝topic_state_repository，非 label 文字
    比對）。以 workspace 成員 CTE 與 patent_ids 交集取明細，只回 patent_id／patent_number／
    title／country_code／applicant_display_name，依 patent_id 升冪、可分頁。patent_ids 為空
    直接回空清單（不發查詢）。呼叫端須先確認 workspace 與 topic 存在。
    """
    if not patent_ids:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    params = {
        "workspace_id": workspace_id,
        "pids": list(patent_ids),
        "limit": limit,
        "offset": offset,
        "kw_lookup": patent_queries.keyword_lookup_pattern(keyword),
    }
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_TOPIC_PATENTS_COUNT_SQL, params)
            total = int(cur.fetchone()["total"])
            cur.execute(_TOPIC_PATENTS_ITEMS_SQL, params)
            items = cur.fetchall()
    # 補分通道主題欄（2026-07-27）：原本這裡完全沒有主題欄位，點進單一主題後
    # 技術分類／功效分類兩欄全空——分通道欄當初只加在 list_workspace_patents。
    # source_field=None：此清單已鎖定在某主題，沒有「當前通道」的單一欄語意。
    _attach_topic_columns(items, workspace_id=workspace_id)
    # 🔴 2026-08-18：推導型顯示欄位當初也只加在 list_workspace_patents，
    #    於是分類區點進主題後「專利種類」「專利狀態」整欄空白——與上面那條註解
    #    完全同型的漏，在同一支函式上發生第二次。推導已收斂到
    #    patent_queries.attach_display_fields（唯一定義處），此處只消費。
    patent_queries.attach_display_fields(items)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
