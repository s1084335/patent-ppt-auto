from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchTermField:
    """定義一個可搜尋欄位與其 SQL 來源。"""

    field_key: str
    field_label: str
    source_sql: str
    is_primary: bool = False
    source_rank: int = 100


# 使用者可能拿來搜尋的專利欄位只在這裡定義一次；SQL refresh 與契約測試都讀這份。
SEARCH_TERM_FIELDS: tuple[SearchTermField, ...] = (
    SearchTermField("grant_number", "授權公告號", 'p."授權公告號"', True, 10),
    SearchTermField("publication_number", "未審查的公開號", 'p."未審查的公開號"', True, 11),
    SearchTermField("publication_number_normalized", "未審查的公開號(轉換後)", 'p."未審查的公開號(轉換後)"', True, 12),
    SearchTermField("application_number", "申請號", 'p."申請號"', True, 13),
    SearchTermField("application_number_normalized", "申請號(轉換後)", 'p."申請號(轉換後)"', True, 14),
    SearchTermField("title", "發明名稱", "p.title", True, 20),
    SearchTermField("title_original", "發明名稱(原文)", "p.title_original", True, 21),
    SearchTermField("abstract", "摘要", "p.abstract", False, 30),
    SearchTermField("abstract_original", "摘要(原文)", 'p."摘要(原文)"', False, 31),
    SearchTermField("country_code", "國別", "p.country_code", False, 40),
    SearchTermField("legal_status", "法律狀態", "p.legal_status", False, 41),
    SearchTermField("patent_type", "專利類型", "p.patent_type", False, 42),
    SearchTermField("document_kind", "文獻種類", "p.document_kind", False, 43),
    SearchTermField("raw_applicant", "申請人", 'pp."申請人"', True, 50),
    SearchTermField("standardized_applicant", "標準化申請人", 'pp."標準化申請人"', True, 51),
    SearchTermField("applicant_display_name", "報表申請人顯示名", "rpb.applicant_display_name", True, 52),
    SearchTermField("applicant_code", "申請人代表碼", 'pp."申請人代表碼"', False, 53),
    SearchTermField("current_owner", "最近專利權人", 'pp."最近專利權人[US,JP,KR,CN,CA,AU]"', True, 60),
    SearchTermField("standardized_current_owner", "標準當前專利權人", 'pp."標準當前專利權人[US,JP,KR,CN,CA,AU]"', True, 61),
    SearchTermField("current_owner_display_name", "報表當前權利人顯示名", "rpb.current_assignee_display_name", True, 62),
    SearchTermField("current_owner_code", "標準當前專利權人代碼", 'pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"', False, 63),
    SearchTermField("recent_assignee", "最近受讓人", 'pp."最近受讓人[US,KR,CN]"', True, 70),
    SearchTermField("recent_assignee_display_name", "報表最近受讓人顯示名", "rpb.recent_assignee_display_name", True, 71),
    SearchTermField("inventor", "發明人", 'pp."發明人"', True, 80),
    SearchTermField("orig_ipc_main", "Orig. IPC(Main)", 'p."Orig. IPC(Main)"', False, 90),
    SearchTermField("curr_ipc_main", "Curr. IPC(Main)", 'p."Curr. IPC(Main)"', False, 91),
    SearchTermField("orig_cpc_main", "Orig. CPC(Main)", 'p."Orig. CPC(Main)"', False, 92),
    SearchTermField("curr_cpc_main", "Curr. CPC(Main)", 'p."Curr. CPC(Main)"', False, 93),
    SearchTermField("orig_ipc_all", "Orig. IPC(All)", 'pa."Orig. IPC(All)"', False, 94),
    SearchTermField("curr_ipc_all", "Curr. IPC(All)", 'pa."Curr. IPC(All)"', False, 95),
    SearchTermField("orig_cpc_all", "Orig. CPC(All)", 'pa."Orig. CPC(All)"', False, 96),
    SearchTermField("curr_cpc_all", "Curr. CPC(All)", 'pa."Curr. CPC(All)"', False, 97),
    SearchTermField("classification_label", "分類標籤", 'pa."分類標籤"', False, 98),
)


def _sql_literal(value: str) -> str:
    """將 Python 字串安全放進本模組產生的 SQL literal。"""
    return "'" + value.replace("'", "''") + "'"


def _field_values_sql() -> str:
    rows = []
    for field in SEARCH_TERM_FIELDS:
        rows.append(
            "("
            f"{_sql_literal(field.field_key)}, "
            f"{_sql_literal(field.field_label)}, "
            f"{field.source_sql}, "
            f"{'true' if field.is_primary else 'false'}, "
            f"{field.source_rank}"
            ")"
        )
    return ",\n            ".join(rows)


REFRESH_PATENT_SEARCH_TERMS_SQL = f"""
TRUNCATE TABLE derived_layer.patent_search_terms;

WITH patent_attributes_one AS (
    SELECT
        patent_id,
        max(NULLIF(BTRIM("Orig. IPC(All)"), '')) AS "Orig. IPC(All)",
        max(NULLIF(BTRIM("Curr. IPC(All)"), '')) AS "Curr. IPC(All)",
        max(NULLIF(BTRIM("Orig. CPC(All)"), '')) AS "Orig. CPC(All)",
        max(NULLIF(BTRIM("Curr. CPC(All)"), '')) AS "Curr. CPC(All)",
        max(NULLIF(BTRIM("分類標籤"), '')) AS "分類標籤"
    FROM core_layer.patent_attributes
    GROUP BY patent_id
),
raw_terms AS (
    SELECT
        p.id AS patent_id,
        term.field_key,
        term.field_label,
        term.raw_value,
        term.is_primary,
        term.source_rank
    FROM core_layer.patents p
    LEFT JOIN core_layer.patent_people pp ON pp.patent_id = p.id
    LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
    LEFT JOIN patent_attributes_one pa ON pa.patent_id = p.id
    CROSS JOIN LATERAL (
        VALUES
            {_field_values_sql()}
    ) AS term(field_key, field_label, raw_value, is_primary, source_rank)
),
split_terms AS (
    SELECT
        patent_id,
        field_key,
        field_label,
        is_primary,
        source_rank,
        NULLIF(BTRIM(part), '') AS term_text
    FROM raw_terms
    CROSS JOIN LATERAL regexp_split_to_table(COALESCE(raw_value, ''), '\\s*\\|\\s*') AS part
),
normalized_terms AS (
    SELECT
        patent_id,
        field_key,
        field_label,
        term_text,
        lower(regexp_replace(BTRIM(term_text), '\\s+', ' ', 'g')) AS term_lookup,
        is_primary,
        source_rank
    FROM split_terms
    WHERE term_text IS NOT NULL
)
INSERT INTO derived_layer.patent_search_terms (
    patent_id,
    field_key,
    field_label,
    term_text,
    term_lookup,
    is_primary,
    source_rank
)
SELECT DISTINCT
    patent_id,
    field_key,
    field_label,
    term_text,
    term_lookup,
    is_primary,
    source_rank
FROM normalized_terms
WHERE term_lookup IS NOT NULL
ON CONFLICT (patent_id, field_key, term_lookup) DO NOTHING
"""


def refresh_patent_search_terms() -> dict[str, Any]:
    """重建 derived_layer.patent_search_terms，供瀏覽搜尋與 MCP 取證共用。"""
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database refresh. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(REFRESH_PATENT_SEARCH_TERMS_SQL)
            cur.execute("SELECT count(*) FROM derived_layer.patent_search_terms")
            row_count = cur.fetchone()[0]
        conn.commit()
    return {"status": "refreshed", "patent_search_terms_rows": row_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh derived_layer.patent_search_terms.")
    parser.parse_args()
    summary = refresh_patent_search_terms()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
