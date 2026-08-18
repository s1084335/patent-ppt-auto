"""既有專利庫的唯讀查詢服務（供案件比對選取被比對專利、專利總覽跨 workspace 顯示）。

集中專利號搜尋與全庫分頁清單的只讀 SQL。設計約束：
- 只讀不寫；沿用 get_pool() 借還連線與 dict_row，row_factory 設在 cursor 上避免污染池連線。
- 專利號機制沿用 workspace_queries 的六欄 COALESCE（授權公告號 / 審查的公告號 /
  未審查的公開號(轉換後) / 未審查的公開號 / 申請號(轉換後) / 申請號），不綁單一號格式或欄位。
- 單一 SQL 批次查 + LIMIT 上限，不逐筆查、不全表掃（limit 上限由 API 層以 le=200 擋）。
- applicant_display_name 由 derived_layer.report_patent_base LEFT JOIN 取（未涵蓋者回 NULL）。
- workspace 歸屬（list_patents）以「本頁 patent_id 一次批次反查」求得，不對每筆專利另發查詢
  （避免 N+1）；查詢次數固定三條（count / items / membership），不隨資料量成長。
- 顯示欄位（2026-07-23 定案，見 `.agents/context/patent-display-spec.md`）分散在
  core_layer.patents／patent_people／derived_layer.report_patent_base，一次 JOIN 帶回，
  前端不逐筆補查（0046 起顯示欄位已無一欄來自 patent_attributes）；主附圖 bytea 一律不進清單（只回 has_figure 布林）。
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from backend.app.clustering.sources import source_fields
from backend.app.db import job_repository
from backend.app.db.connection import get_pool
from backend.app.transforms.patent_kind import patent_kind
from backend.app.mappings.legal_status import (
    TW_LEGAL_STATUS_ALLOWED,
    display_legal_status,
    validate_tw_legal_status,
)
from backend.app.repositories.topic_state_repository import (
    PostgresTopicStateRepository,
    TopicStateNotFoundError,
)
from backend.app.transforms.patent_numbers import display_number_sql

# ── 顯示欄位單一事實來源 ──────────────────────────────────────────────
# 2026-07-23 定案的專利顯示欄位：回應 key → 取值 SQL 運算式。清單與詳情共用同一組欄位
# （使用者定案「不做兩套」），前端亦由單一欄位定義驅動表頭與資料列。
# 分三份 dict 對應三處來源，避免把來源混在一起看不出落點：
#   _PATENT_FIELDS    ── core_layer.patents（主表，一對一）
#   _PEOPLE_FIELDS    ── core_layer.patent_people（一對一，LEFT JOIN）
# 新增／移除顯示欄位只需改這幾份 dict，SQL 投影與回應 key 自動跟著變（不寫死欄位數）。
# workspace 專利清單（分類區）匯入同一份定義，兩區共用同一組欄位，不做兩套。

# patents 主表欄位：直接投影，NULLIF(BTRIM(...)) 把 WIPS 的空白字元正規化成 NULL
# （實測踩坑：WIPS 空欄填的是 ' ' 而非 NULL，不正規化會讓前端顯示一格空白而非「無值」）。
# 運算式中的 {p} / {pp} 是表別名佔位（由 display_projection 以 str.format 填入），
# 讓不同呼叫端（總覽 CTE、workspace 成員 CTE）能沿用同一份定義而不必統一別名。
_PATENT_FIELDS: dict[str, str] = {
    "country_code": "NULLIF(BTRIM({p}.country_code), '')",
    "patent_type": "NULLIF(BTRIM({p}.patent_type), '')",
    # 專利種類推導（五之三）：patent_kind 需要兩欄組合，document_kind 一併帶出。
    "document_kind": "NULLIF(BTRIM({p}.document_kind), '')",
    "legal_status": "NULLIF(BTRIM({p}.legal_status), '')",
    "title": "NULLIF(BTRIM({p}.title), '')",
    "title_original": "NULLIF(BTRIM({p}.title_original), '')",
    "abstract": "NULLIF(BTRIM({p}.abstract), '')",
    # 申請號／公開號的**顯示值轉換後優先**（2026-08-04 治本：TW 扣 1911 的機制
    # 要在顯示端生效；非 TW 案轉換欄＝原值，無副作用）。原值仍在 DB 供查證。
    "application_number": 'COALESCE(NULLIF(BTRIM({p}."申請號(轉換後)"), \'\'), NULLIF(BTRIM({p}."申請號"), \'\'))',
    "application_date": "{p}.application_date",
    "application_year": "{p}.application_year",
    "publication_number": 'COALESCE(NULLIF(BTRIM({p}."未審查的公開號(轉換後)"), \'\'), NULLIF(BTRIM({p}."未審查的公開號"), \'\'))',
    "grant_number": 'NULLIF(BTRIM({p}."授權公告號"), \'\')',
    "orig_ipc_main": 'NULLIF(BTRIM({p}."Orig. IPC(Main)"), \'\')',
    # 文獻備註 0032 起搬到 patents 主表（一專利一列，AI 回寫直接 WHERE id）；
    # 不再從 patent_attributes「最新非空」取，避免多列選列不一致。
    "patent_note": 'NULLIF(BTRIM({p}."文獻備註"), \'\')',
    # ── 欄位重分類（0046，2026-08-06）─────────────────────────────
    # 這 8 欄原本走 `_attribute_pick` 的「最新非空」相關子查詢，0046 起已在 patents。
    # ⚠ 為什麼要搬：`patent_attributes` 是一 raw_record 一列的寬表，每個讀取端都得
    # 自己實作一次「哪一列才算數」——`patent_queries` 用 `raw_record_id DESC`、
    # `refresh_report_patent_base` 用另一個子查詢，**選列規則散在多處且不保證一致**。
    # 搬進 patents 後一專利一列，canonical value 只有一個，選列問題消失。
    "abstract_original": 'NULLIF(BTRIM({p}."摘要(原文)"), \'\')',
    "publication_date": 'NULLIF(BTRIM({p}."未審查的公開日"), \'\')',
    "grant_date": 'NULLIF(BTRIM({p}."授權公告日"), \'\')',
    "priority_number": 'NULLIF(BTRIM({p}."優先權號"), \'\')',
    "priority_country": 'NULLIF(BTRIM({p}."優先權國家"), \'\')',
    "priority_date": 'NULLIF(BTRIM({p}."優先權日"), \'\')',
    "detail_url": 'NULLIF(BTRIM({p}."詳細查看連結(登入)"), \'\')',
    "pdf_url": 'NULLIF(BTRIM({p}."文圖像文件(PDF)連結"), \'\')',
    # 授權公告年＝授權公告日的衍生欄（前端詳情與年度報表同一口徑）。
    # ⚠ 只取「授權公告日」，不 fallback 到公開日或審查公告日——沿 0046 前的既有規則。
    "grant_year": (
        'CASE WHEN BTRIM(COALESCE({p}."授權公告日", \'\')) ~ \'^[0-9]{{4}}\' '
        'THEN SUBSTRING(BTRIM({p}."授權公告日") FROM 1 FOR 4)::integer END'
    ),
}

# patent_people：patent_id 為 PK（一對一），LEFT JOIN 即可，不會放大列數。
# ⚠ 公司名欄（申請人／專利權人）**原始字面**改投影成 *_original，供詳情層對照；
# 列表顯示的收斂名走 _REPORT_BASE_FIELDS（2026-07-26 定案，見下）。
# 發明人是自然人、不走公司名收斂管線，維持原欄名不動。
_PEOPLE_FIELDS: dict[str, str] = {
    "applicant_original": 'NULLIF(BTRIM({pp}."申請人"), \'\')',
    "inventor": 'NULLIF(BTRIM({pp}."發明人"), \'\')',
    "current_owner_original": 'NULLIF(BTRIM({pp}."最近專利權人[US,JP,KR,CN,CA,AU]"), \'\')',
    "recent_assignee_original": 'NULLIF(BTRIM({pp}."最近受讓人[US,KR,CN]"), \'\')',
}

# derived_layer.report_patent_base：公司名的**正規化（收斂）顯示名**，與報表同一口徑。
# 2026-07-26 使用者定案：表格顯示正規化值，原始字面留在詳情層（*_original）。
# 動因：先前表格顯示原始字面、報表顯示收斂名，同一件專利兩處兩個名字；且搜尋走
# applicant_display_name，使用者照表格字面搜尋會搜不到。
# 收斂規則（代碼→confirmed 對照名→別稱→庫內統計名→標準化名→原值）集中在
# refresh_report_patent_base 的 COALESCE，此處只取結果，**不在這裡再算一套**。
# 呼叫端須自行 LEFT JOIN derived_layer.report_patent_base（別名 report_base_alias）；
# 未涵蓋的專利回 NULL，沿「欄位一律呈現、無值空白」通則。
_REPORT_BASE_FIELDS: dict[str, str] = {
    "applicant": "{rpb}.applicant_display_name",
    "current_owner": "{rpb}.current_assignee_display_name",
    "recent_assignee": "{rpb}.recent_assignee_display_name",
}

# ⚠ 0046（2026-08-06）**移除了 `_ATTRIBUTE_FIELDS` 與 `_ATTRIBUTE_YEAR_FIELDS`**。
# 那 8 欄＋衍生的 grant_year 已搬進 `core_layer.patents`（見 _PATENT_FIELDS 尾段），
# 顯示欄位不再有任何一欄來自 `patent_attributes`——連同 `_attribute_pick` /
# `_attribute_year_pick` 兩支子查詢組裝函式一併刪除（無人使用的轉手層）。
# 需要重新讀 attributes 時，請先確認該欄是否**真的**該留在 attributes（規格：
# 只有「完全沒被程式使用」的 WIPS 欄位才放那裡），而不是直接把機制加回來。


def topic_label_key(source_field: str) -> str:
    """回傳某分群通道在 API 回應中的分類欄 key（如 topic_label_wips_independent_claims）。

    2026-07-24 使用者定案：分類標籤拆成技術／功效兩獨立欄，不在同一欄併呈兩個值。
    欄名由通道常數推導，新增通道時不需改此處也不需改前端（前端同樣走 source_fields()）。
    """
    return f"topic_label_{source_field}"


def display_field_keys() -> tuple[str, ...]:
    """回傳全部顯示欄位的回應 key（欄位清單的唯一來源，供其他模組與測試取用）。

    專利總覽與分類區共用同一組欄位（使用者定案「不做兩套」），兩邊都由此推導，
    不各自維護一份欄名清單，也不寫死欄位數。
    """
    return (*_PATENT_FIELDS, *_PEOPLE_FIELDS, *_REPORT_BASE_FIELDS)


def attach_display_fields(items: list[dict[str, Any]]) -> None:
    """補上「查完才推導」的顯示欄位（原地修改）。

    `patent_kind_display`／`legal_status_display` 不是 SQL 欄位，是查回來之後在
    Python 推導的。⚠ 這段原本**抄在每一支清單函式裡**，於是漏掉
    `list_topic_patents`——分類區點進某個主題後「專利種類」「專利狀態」整欄空白
    （2026-08-18 使用者回報）。同型的事在同一支函式上已經發生過一次
    （分通道主題欄當初也只加在 `list_workspace_patents`）。

    落點在此（`display_projection`／`display_field_keys` 的同一個模組）：
    定義欄位的地方就負責把欄位補完，新增清單只要呼叫這一支。
    """
    for it in items:
        # 顯示字面：簡→繁只在 mappings 定義一次，前端只消費此欄。
        it["legal_status_display"] = display_legal_status(it.get("legal_status"))
        # 專利種類：唯一定義處 transforms/patent_kind 推導。
        it["patent_kind_display"] = patent_kind(it)


def display_projection(
    *,
    patents_alias: str = "p",
    people_alias: str = "pp",
    report_base_alias: str = "rpb",
) -> str:
    """把三處顯示欄位定義攤成 SQL 投影片段（欄位清單的唯一 SQL 來源）。

    alias 可覆寫，讓 workspace 專利清單（workspace_queries）用相同投影而不必改自己的
    表別名——兩區的欄位定義因此只有這一份。呼叫端須自行 LEFT JOIN patent_people
    （別名 people_alias）與 derived_layer.report_patent_base（別名 report_base_alias）。
    ⚠ 0046 起全部欄位都是直接投影，不再有相關子查詢。
    """
    parts = [
        f"{expr.format(p=patents_alias)} AS {key}" for key, expr in _PATENT_FIELDS.items()
    ]
    parts += [
        f"{expr.format(pp=people_alias)} AS {key}" for key, expr in _PEOPLE_FIELDS.items()
    ]
    parts += [
        f"{expr.format(rpb=report_base_alias)} AS {key}"
        for key, expr in _REPORT_BASE_FIELDS.items()
    ]
    return ",\n        ".join(parts)


# 顯示號規則唯一定義處在 transforms.patent_numbers（2026-08-04 治本收斂）；
# 以 CTE 先算出 patent_number 與 applicant，供外層對 patent_number／title 過濾。
# 號搜（search_patents）與全庫清單（list_patents）共用此 CTE，不重寫兩份專利號規則。
_CANDIDATES_CTE = f"""
WITH candidates AS (
    SELECT
        p.id AS patent_id,
        {display_number_sql("p")} AS patent_number,
        -- 只回「有無代表圖」布林，不把 bytea 內容帶進清單（清單一頁 200 筆會拖回數 MB）；
        -- 實際圖片由前端逐筆走 GET /patents/{{id}}/figure 惰性載入。
        (p."主附圖" IS NOT NULL) AS has_figure,
        rpb.applicant_display_name AS applicant_display_name,
        {display_projection()}
    FROM core_layer.patents p
    LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
    -- patent_people 的 PK 為 patent_id（一對一），LEFT JOIN 不會放大列數。
    LEFT JOIN core_layer.patent_people pp ON pp.patent_id = p.id
)
"""


# 清單／號搜共用的投影欄位清單：由顯示欄位定義推導，不逐個寫死欄名
# （後端新增顯示欄位時，SELECT 清單自動跟著長）。
_LIST_SELECT_KEYS = ",\n       ".join(
    (
        "patent_id",
        "patent_number",
        "has_figure",
        "applicant_display_name",
        *display_field_keys(),
    )
)

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
SELECT {_LIST_SELECT_KEYS}
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


# 單筆代表圖取回：只取 "主附圖" 一欄（不 SELECT *，避免把主表其他大欄一併拖回）。
# 🔴 2026-08-07 反悔機制：面板改列**全部** TW 案（含已登錄者才有東西可反悔），
# 未登錄與已登錄由 items 的 legal_status 欄區分；pending 數另計。
_PENDING_TW_STATUS_WHERE = """
WHERE p.country_code = 'TW'
  AND (
      %(workspace_id)s::bigint IS NULL
      OR EXISTS (
          SELECT 1
          FROM app_layer.workspaces w
          JOIN LATERAL jsonb_array_elements(w.patent_ids_json) AS m(pid) ON TRUE
          WHERE w.workspace_id = %(workspace_id)s::bigint
            AND (m.pid)::bigint = p.id
      )
  )
"""

_PENDING_TW_STATUS_ITEMS_SQL = f"""
SELECT
    p.id AS patent_id,
    {display_number_sql("p")} AS patent_number,
    p.title,
    p.country_code,
    rpb.applicant_display_name,
    NULLIF(BTRIM(p.legal_status), '') AS legal_status
FROM core_layer.patents p
LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
{_PENDING_TW_STATUS_WHERE}
ORDER BY p.id
LIMIT %(limit)s OFFSET %(offset)s
"""

_PENDING_TW_STATUS_COUNT_SQL = f"""
SELECT count(*) AS total,
       count(*) FILTER (WHERE NULLIF(BTRIM(p.legal_status), '') IS NULL) AS pending_total
FROM core_layer.patents p
{_PENDING_TW_STATUS_WHERE}
"""

_REGISTER_TW_STATUS_SQL = """
WITH target AS (
    SELECT id, legal_status
    FROM core_layer.patents
    WHERE id = %(patent_id)s AND country_code = 'TW'
    FOR UPDATE
), updated AS (
    UPDATE core_layer.patents p
    SET legal_status = %(status)s::text,
        legal_status_history = COALESCE(p.legal_status_history, '[]'::jsonb)
            || jsonb_build_array(jsonb_build_object(
                'from_status', target.legal_status,
                'to_status', %(status)s::text,
                'changed_at', to_jsonb(now())
            ))
    FROM target
    WHERE p.id = target.id
    RETURNING p.id AS patent_id, target.legal_status AS from_status, p.legal_status AS to_status
)
SELECT patent_id, from_status, to_status FROM updated
"""


class TwLegalStatusNotFoundError(ValueError):
    """找不到指定專利。"""


class TwLegalStatusCountryError(ValueError):
    """指定專利不是 TW 專利。"""


class TwLegalStatusConflictError(ValueError):
    """指定 TW 專利已經登錄 legal_status。"""


_PATENT_FIGURE_SQL = 'SELECT "主附圖" AS figure FROM core_layer.patents WHERE id = %(pid)s'


def resolve_topic_workspace_id() -> int | None:
    """**唯一切換點**：決定專利總覽的分類標籤要取哪個 workspace 的分群主題。

    2026-07-24 現況：使用者尚未定案「總覽顯示全庫 workspace 的主題，還是各 workspace 的
    主題」。暫採**全庫 workspace**（`is_global`，0028 落點）——總覽是跨 workspace 的全庫視角，
    一件專利可屬多個 workspace、各自有不同分群，取單一 workspace 才有唯一解；全庫 workspace
    的成員涵蓋全部專利，是唯一對每筆專利都成立的來源。

    尚未建立全庫 workspace 時回 None，此時兩個分類欄一律為空（沿「尚未分群留空」定案）。
    使用者若改採「各 workspace 主題」，只需改本函式（或由呼叫端傳
    `list_patents(topic_workspace_id=...)` 覆寫），SQL 與前端都不用動。
    """
    from backend.app.app_layer import global_workspace

    return global_workspace.get_global_workspace_id()


def _topic_labels_by_patent(workspace_id: int | None) -> dict[str, dict[int, str]]:
    """取各分群通道的 patent_id → topic label 映射，供分類標籤兩欄合併回清單。

    回 {source_field: {patent_id: label}}。通道清單走 clustering.sources 的 source_fields()
    白名單（不寫死技術／功效字串）；某通道尚無分群 run 時該通道回空 dict，對應欄位留空。
    每個通道各取一次 topic state（通道數固定為 2，與專利筆數無關，非 N+1）。
    """
    labels: dict[str, dict[int, str]] = {sf: {} for sf in source_fields()}
    if workspace_id is None:
        return labels
    repository = PostgresTopicStateRepository()
    for source_field in source_fields():
        try:
            state = repository.get_latest_topic_state(workspace_id, source_field)
        except (TopicStateNotFoundError, ValueError):
            # 該通道尚未分群：沿「尚未分群留空」定案，整欄為空，總覽照常顯示。
            continue
        for topic in state.get("topics", []):
            label = topic.get("label")
            if not label:
                continue
            for pid in topic.get("patent_ids", []):
                labels[source_field][int(pid)] = label
    return labels


def get_patent_figure(patent_id: int) -> bytes | None:
    """取單筆專利的代表圖位元組；查無專利或該筆無圖皆回 None（由 API 層轉 404）。"""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_PATENT_FIGURE_SQL, {"pid": patent_id})
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return bytes(row[0])


def allowed_tw_legal_statuses() -> list[str]:
    """回傳 TW 人工登錄狀態清單，供前端產生下拉選單。"""
    return list(TW_LEGAL_STATUS_ALLOWED)


def list_pending_tw_legal_status_patents(
    *,
    workspace_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列出尚未人工登錄狀態的 TW 專利。"""
    params = {"workspace_id": workspace_id, "limit": limit, "offset": offset}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_PENDING_TW_STATUS_COUNT_SQL, params)
        counts = cur.fetchone()
        cur.execute(_PENDING_TW_STATUS_ITEMS_SQL, params)
        items = cur.fetchall()
    return {
        "items": items,
        "total": int(counts["total"]),
        # 未登錄件數另計：面板列全部 TW（反悔機制），標題仍要能講「還缺幾件」。
        "pending_total": int(counts["pending_total"]),
        "limit": limit,
        "offset": offset,
        "allowed_statuses": allowed_tw_legal_statuses(),
    }


def normalize_tw_status_input(value: str | None) -> str | None:
    """反悔機制的輸入正規化：None＝清回空值（未知桶）；字串走值域檢查。"""
    if value is None:
        return None
    return validate_tw_legal_status(value)


def _classify_tw_status_failure(patent_id: int) -> None:
    """把條件式更新未命中的狀態轉成明確錯誤。

    🔴 2026-08-07 反悔機制：已有值可改可清，「已有狀態」不再是錯誤——
    未命中只剩查無專利與非 TW 兩種。"""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT country_code FROM core_layer.patents WHERE id = %s",
            (patent_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise TwLegalStatusNotFoundError(f"patent {patent_id} not found")
    raise TwLegalStatusCountryError(f"patent {patent_id} is not a TW patent")


def enqueue_tw_legal_status_refresh(*, workspace_id: int | None = None) -> dict[str, Any]:
    """只排入法律狀態相關報表的刷新，不重寫狀態或 history。"""
    # ⚠ 2026-08-09：原本刷新 `lifecycle`（已刪）。狀態登錄後要更新的是「法律狀態」
    # 相關報表，由 `country_distribution`（國別×法律狀態）承接。
    payload: dict[str, Any] = {"report_names": ["country_distribution"]}
    if workspace_id is not None:
        payload["workspace_id"] = workspace_id
    job = job_repository.create_job(
        "report_generate",
        payload,
        workspace_id=workspace_id,
    )
    return {"refresh_status": "queued", "refresh_job_id": job.job_id}


def register_tw_legal_status(
    *,
    patent_id: int,
    legal_status: str | None,
    workspace_id: int | None = None,
) -> dict[str, Any]:
    """登錄／修改／清除 TW 專利狀態，並在提交後背景刷新法律狀態報表。

    🔴 2026-08-07 反悔機制：已有值可改成別的值、可清回 None（未知桶）；
    每次異動 append `legal_status_history`。只限 TW 案。"""
    status = normalize_tw_status_input(legal_status)
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_REGISTER_TW_STATUS_SQL, {"patent_id": patent_id, "status": status})
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                _classify_tw_status_failure(patent_id)
            cur.execute(
                "UPDATE derived_layer.report_patent_base "
                "SET legal_status = %s WHERE patent_id = %s",
                (status, patent_id),
            )
        conn.commit()

    try:
        refresh = enqueue_tw_legal_status_refresh(workspace_id=workspace_id)
    except Exception as exc:  # noqa: BLE001 - 狀態已提交，刷新失敗不可 rollback
        refresh = {"refresh_status": "enqueue_failed", "refresh_error": str(exc)}
    return {
        "saved": True,
        "patent_id": int(row["patent_id"]),
        # 清除時為 None——不得 str() 硬轉成 'None' 字串。
        "legal_status": row["to_status"],
        **refresh,
    }


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
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_PATENT_SEARCH_SQL, params)
        items = cur.fetchall()
    return {"items": items}


def list_patents(
    *,
    limit: int = 50,
    offset: int = 0,
    keyword: str | None = None,
    topic_workspace_id: int | None = -1,
) -> dict[str, Any]:
    """分頁列出全庫專利（不分 workspace），每筆含完整顯示欄位與所屬 workspace。

    供專利總覽跨 workspace 顯示：資料一律分頁（limit 上限由 API 層以 le=200 擋），
    不一次撈全庫。keyword 去空白後為空視為不過濾，有值時對 patent_number／title／
    applicant_display_name 做 ILIKE。

    回 {items, total, limit, offset}；每筆含 patent_id／patent_number／has_figure／
    applicant_display_name、2026-07-23 定案的顯示欄位（_PATENT_FIELDS／_PEOPLE_FIELDS／
    _ATTRIBUTE_FIELDS 三份定義驅動，來源無值一律回 None 但 key 必在）、
    各分群通道的分類標籤（topic_label_key(source_field)，尚未分群為 None），
    以及 workspaces（[{workspace_id, workspace_name}]，不屬任何 workspace 者為空陣列）。

    topic_workspace_id：分類標籤取哪個 workspace 的分群主題。預設哨兵 -1 表示「由
    resolve_topic_workspace_id() 決定」（唯一切換點）；傳 None 則不取主題（兩欄皆空）。

    效率：SQL 固定三條（count / items / 本頁 patent_id 批次反查 workspace 歸屬），
    不對每筆專利另發查詢（無 N+1）；patent_people 與 report_patent_base 皆以
    純量子查詢隨 items SQL 一起求值，不逐筆補查；主附圖 bytea 不進清單。
    歸屬與分類標籤在 Python 層以 dict 合併回清單。
    """
    cleaned = keyword.strip() if keyword else None
    kw = f"%{cleaned}%" if cleaned else None
    params = {"kw": kw, "limit": limit, "offset": offset}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
    # 分類標籤：技術／功效兩通道各一欄，在 Python 層以指派 dict 合併（不改成員查詢 SQL）。
    # 哨兵 -1＝未指定，走唯一切換點決定來源；明確傳 None＝不取主題。
    resolved_ws = resolve_topic_workspace_id() if topic_workspace_id == -1 else topic_workspace_id
    topic_labels = _topic_labels_by_patent(resolved_ws)
    attach_display_fields(items)   # 推導型顯示欄位：唯一定義處
    for it in items:
        it["workspaces"] = membership.get(it["patent_id"], [])
        for source_field, by_patent in topic_labels.items():
            it[topic_label_key(source_field)] = by_patent.get(it["patent_id"])
    return {"items": items, "total": total, "limit": limit, "offset": offset}
