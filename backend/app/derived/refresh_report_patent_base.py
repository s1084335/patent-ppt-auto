from __future__ import annotations

import argparse
import json
from typing import Any


REFRESH_SQL = """
TRUNCATE TABLE derived_layer.report_patent_base;

WITH source_one AS (
    SELECT DISTINCT ON (ps.patent_id)
        ps.patent_id,
        ps.dedupe_key
    FROM core_layer.patent_sources ps
    ORDER BY ps.patent_id, ps.id DESC
),
base AS (
    SELECT
        p.id AS patent_id,
        so.dedupe_key,
        p."授權公告號",
        p."審查的公告號",
        p."未審查的公開號",
        p."申請號",
        p.country_code,
        p.application_date,
        p.application_year,
        p.publication_year,
        p.title,
        p."Curr. IPC(Main)",
        p."Curr. CPC(Main)",
        pp."申請人",
        pp."申請人國籍",
        pp."標準化申請人",
        pp."發明人",
        pp."發明人國籍",
        pp."最近專利權人[US,JP,KR,CN,CA,AU]",
        pp."標準當前專利權人[US,JP,KR,CN,CA,AU]",
        pp."最近受讓人[US,KR,CN]",
        p."主權項",
        p."獨立項[KR,JP,US,CN,EP,IN]",
        p."所有權利要求[JP,KR,CN]"
    FROM core_layer.patents p
    LEFT JOIN source_one so ON so.patent_id = p.id
    LEFT JOIN core_layer.patent_people pp ON pp.patent_id = p.id
)
INSERT INTO derived_layer.report_patent_base (
    patent_id,
    dedupe_key,
    "授權公告號",
    "審查的公告號",
    "未審查的公開號",
    "申請號",
    country_code,
    application_date,
    application_year,
    publication_year,
    title,
    "Curr. IPC(Main)",
    "Curr. CPC(Main)",
    "申請人",
    "申請人國籍",
    "標準化申請人",
    applicant_display_name,
    "發明人",
    "發明人國籍",
    "最近專利權人[US,JP,KR,CN,CA,AU]",
    "標準當前專利權人[US,JP,KR,CN,CA,AU]",
    current_assignee_display_name,
    "最近受讓人[US,KR,CN]",
    recent_assignee_display_name,
    "主權項",
    "獨立項[KR,JP,US,CN,EP,IN]",
    "所有權利要求[JP,KR,CN]",
    "比對用權利要求"
)
SELECT
    b.patent_id,
    b.dedupe_key,
    b."授權公告號",
    b."審查的公告號",
    b."未審查的公開號",
    b."申請號",
    b.country_code,
    b.application_date,
    b.application_year,
    b.publication_year,
    b.title,
    b."Curr. IPC(Main)",
    b."Curr. CPC(Main)",
    b."申請人",
    b."申請人國籍",
    b."標準化申請人",
    COALESCE(applicant_alias."公司名稱", NULLIF(BTRIM(b."標準化申請人"), ''), NULLIF(BTRIM(b."申請人"), '')) AS applicant_display_name,
    b."發明人",
    b."發明人國籍",
    b."最近專利權人[US,JP,KR,CN,CA,AU]",
    b."標準當前專利權人[US,JP,KR,CN,CA,AU]",
    COALESCE(owner_alias."公司名稱", NULLIF(BTRIM(b."標準當前專利權人[US,JP,KR,CN,CA,AU]"), ''), NULLIF(BTRIM(b."最近專利權人[US,JP,KR,CN,CA,AU]"), '')) AS current_assignee_display_name,
    b."最近受讓人[US,KR,CN]",
    COALESCE(assignee_alias."公司名稱", NULLIF(BTRIM(b."最近受讓人[US,KR,CN]"), '')) AS recent_assignee_display_name,
    b."主權項",
    b."獨立項[KR,JP,US,CN,EP,IN]",
    b."所有權利要求[JP,KR,CN]",
    COALESCE(NULLIF(BTRIM(b."獨立項[KR,JP,US,CN,EP,IN]"), ''), NULLIF(BTRIM(b."主權項"), ''), NULLIF(BTRIM(b."所有權利要求[JP,KR,CN]"), '')) AS "比對用權利要求"
FROM base b
LEFT JOIN LATERAL (
    SELECT ca."公司名稱"
    FROM derived_layer.company_aliases ca
    WHERE lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) IN (
        lower(regexp_replace(BTRIM(COALESCE(b."標準化申請人", '')), '\\s+', ' ', 'g')),
        lower(regexp_replace(BTRIM(COALESCE(b."申請人", '')), '\\s+', ' ', 'g'))
    )
    ORDER BY
        CASE
            WHEN lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = lower(regexp_replace(BTRIM(COALESCE(b."標準化申請人", '')), '\\s+', ' ', 'g')) THEN 1
            ELSE 2
        END,
        ca.id
    LIMIT 1
) applicant_alias ON true
LEFT JOIN LATERAL (
    SELECT ca."公司名稱"
    FROM derived_layer.company_aliases ca
    WHERE lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) IN (
        lower(regexp_replace(BTRIM(COALESCE(b."標準當前專利權人[US,JP,KR,CN,CA,AU]", '')), '\\s+', ' ', 'g')),
        lower(regexp_replace(BTRIM(COALESCE(b."最近專利權人[US,JP,KR,CN,CA,AU]", '')), '\\s+', ' ', 'g'))
    )
    ORDER BY
        CASE
            WHEN lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = lower(regexp_replace(BTRIM(COALESCE(b."標準當前專利權人[US,JP,KR,CN,CA,AU]", '')), '\\s+', ' ', 'g')) THEN 1
            ELSE 2
        END,
        ca.id
    LIMIT 1
) owner_alias ON true
LEFT JOIN LATERAL (
    SELECT ca."公司名稱"
    FROM derived_layer.company_aliases ca
    WHERE lower(regexp_replace(BTRIM(ca."別稱"), '\\s+', ' ', 'g')) = lower(regexp_replace(BTRIM(COALESCE(b."最近受讓人[US,KR,CN]", '')), '\\s+', ' ', 'g'))
    ORDER BY ca.id
    LIMIT 1
) assignee_alias ON true;
"""


def refresh_report_patent_base() -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database refresh. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(REFRESH_SQL)
            cur.execute("SELECT count(*) FROM derived_layer.report_patent_base")
            row_count = cur.fetchone()[0]
        conn.commit()
    return {"status": "refreshed", "report_patent_base_rows": row_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh derived_layer.report_patent_base from raw/core tables.")
    parser.parse_args()
    summary = refresh_report_patent_base()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
