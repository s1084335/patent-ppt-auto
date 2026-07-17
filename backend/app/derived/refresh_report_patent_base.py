from __future__ import annotations

import argparse
import json
from typing import Any


REFRESH_SQL = """
TRUNCATE TABLE derived_layer.report_patent_base;

WITH source_one AS (
    SELECT DISTINCT ON (ps.patent_id)
        ps.patent_id,
        ps.dedupe_key,
        ps.raw_record_id
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
        pp."申請人代表碼",
        pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]",
        p."主權項",
        p."獨立項[KR,JP,US,CN,EP,IN]",
        p."所有權利要求[JP,KR,CN]",
        p."WIPS同族ID",
        p.legal_status,
        fam."WIPS同族各國家文獻數量(申請為準)",
        pa."EPC有效國家[EP]",
        pa."EPC無效國家[EP]",
        -- 引用數/發明人數是快照值，取最新 raw_record（與 EPC 同一組 pa），驗證純數字才轉型
        CASE WHEN BTRIM(COALESCE(pa."(F1)引用文獻數", '')) ~ '^[0-9]+$'
             THEN BTRIM(pa."(F1)引用文獻數")::integer END AS "(F1)引用文獻數",
        CASE WHEN BTRIM(COALESCE(pa."(B1)引用文獻數", '')) ~ '^[0-9]+$'
             THEN BTRIM(pa."(B1)引用文獻數")::integer END AS "(B1)引用文獻數",
        CASE WHEN BTRIM(COALESCE(pa."發明人數", '')) ~ '^[0-9]+$'
             THEN BTRIM(pa."發明人數")::integer END AS "發明人數"
    FROM core_layer.patents p
    LEFT JOIN source_one so ON so.patent_id = p.id
    LEFT JOIN core_layer.patent_people pp ON pp.patent_id = p.id
    -- EPC 有效/無效兩欄必須成對取自同一（最新）raw_record：
    -- 「空」本身是規則輸入（剛授權判定靠無效國為空、到期判定靠有效國清空），
    -- 若各自取最新非空，到期件的清空會被舊值蓋回，直接算錯。
    LEFT JOIN core_layer.patent_attributes pa
        ON pa.patent_id = p.id AND pa.raw_record_id = so.raw_record_id
    -- 同族明細是家族層級常數（同家族每列相同），可安全取最新非空，
    -- 避免最新來源是精簡匯出（無此欄）時整欄變 NULL。
    LEFT JOIN LATERAL (
        SELECT pa2."WIPS同族各國家文獻數量(申請為準)"
        FROM core_layer.patent_attributes pa2
        WHERE pa2.patent_id = p.id
          AND NULLIF(BTRIM(pa2."WIPS同族各國家文獻數量(申請為準)"), '') IS NOT NULL
        ORDER BY pa2.raw_record_id DESC NULLS LAST, pa2.id DESC
        LIMIT 1
    ) fam ON true
),
-- 代碼對照：同一 WIPS 人名代碼（申請人代表碼／標準當前專利權人代碼）在庫內
-- 可能對到多種名稱寫法（跨檔匯出甚至標準化名也會漂移）。
-- 每個代碼選一種統一輸出：優先取最常見的標準化名，沒有就取最常見的原始名；
-- mode() 平手時依排序取第一個，結果 deterministic。
applicant_code_names AS (
    SELECT
        NULLIF(BTRIM(pp."申請人代表碼"), '') AS person_code,
        COALESCE(
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."標準化申請人"), '')),
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."申請人"), ''))
        ) AS canonical_name
    FROM core_layer.patent_people pp
    WHERE NULLIF(BTRIM(pp."申請人代表碼"), '') IS NOT NULL
    GROUP BY NULLIF(BTRIM(pp."申請人代表碼"), '')
),
owner_code_names AS (
    SELECT
        NULLIF(BTRIM(pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '') AS person_code,
        COALESCE(
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."標準當前專利權人[US,JP,KR,CN,CA,AU]"), '')),
            mode() WITHIN GROUP (ORDER BY NULLIF(BTRIM(pp."最近專利權人[US,JP,KR,CN,CA,AU]"), ''))
        ) AS canonical_name
    FROM core_layer.patent_people pp
    WHERE NULLIF(BTRIM(pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '') IS NOT NULL
    GROUP BY NULLIF(BTRIM(pp."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '')
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
    "比對用權利要求",
    "WIPS同族ID",
    legal_status,
    "WIPS同族各國家文獻數量(申請為準)",
    "EPC有效國家[EP]",
    "EPC無效國家[EP]",
    "(F1)引用文獻數",
    "(B1)引用文獻數",
    "發明人數"
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
    COALESCE(applicant_alias."公司名稱", acn.canonical_name, NULLIF(BTRIM(b."標準化申請人"), ''), NULLIF(BTRIM(b."申請人"), '')) AS applicant_display_name,
    b."發明人",
    b."發明人國籍",
    b."最近專利權人[US,JP,KR,CN,CA,AU]",
    b."標準當前專利權人[US,JP,KR,CN,CA,AU]",
    COALESCE(owner_alias."公司名稱", ocn.canonical_name, NULLIF(BTRIM(b."標準當前專利權人[US,JP,KR,CN,CA,AU]"), ''), NULLIF(BTRIM(b."最近專利權人[US,JP,KR,CN,CA,AU]"), '')) AS current_assignee_display_name,
    b."最近受讓人[US,KR,CN]",
    COALESCE(assignee_alias."公司名稱", NULLIF(BTRIM(b."最近受讓人[US,KR,CN]"), '')) AS recent_assignee_display_name,
    b."主權項",
    b."獨立項[KR,JP,US,CN,EP,IN]",
    b."所有權利要求[JP,KR,CN]",
    COALESCE(NULLIF(BTRIM(b."獨立項[KR,JP,US,CN,EP,IN]"), ''), NULLIF(BTRIM(b."主權項"), ''), NULLIF(BTRIM(b."所有權利要求[JP,KR,CN]"), '')) AS "比對用權利要求",
    b."WIPS同族ID",
    b.legal_status,
    b."WIPS同族各國家文獻數量(申請為準)",
    b."EPC有效國家[EP]",
    b."EPC無效國家[EP]",
    b."(F1)引用文獻數",
    b."(B1)引用文獻數",
    b."發明人數"
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
) assignee_alias ON true
LEFT JOIN applicant_code_names acn
    ON acn.person_code = NULLIF(BTRIM(b."申請人代表碼"), '')
LEFT JOIN owner_code_names ocn
    ON ocn.person_code = NULLIF(BTRIM(b."標準當前專利權人代碼[US,JP,KR,CN,CA,AU]"), '');
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
