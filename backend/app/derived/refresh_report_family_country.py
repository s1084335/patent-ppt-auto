"""重建 derived_layer.report_family_country 與 report_family_quality。

國家佈局報表（現有保護口徑）的資料準備步驟：
    report_patent_base（需先跑 refresh_report_patent_base）
        → backend.app.transforms.family_layout 純函式展開
        → 單一 transaction 內 TRUNCATE + INSERT 兩表
執行順序固定：refresh_report_patent_base → 本模組 → report_engine/chart_runner。

CLI: python -m backend.app.derived.refresh_report_family_country
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from backend.app.transforms.family_layout import build_family_country_dataset

# 從 report_patent_base 取出 family_layout 需要的 7 欄，
# 鍵名對應 build_family_country_dataset 的 canonical key。
SOURCE_SQL = """
SELECT
    patent_id,
    "WIPS同族ID" AS family_id,
    country_code,
    legal_status,
    "WIPS同族各國家文獻數量(申請為準)" AS family_counts,
    "EPC有效國家[EP]" AS epc_valid,
    "EPC無效國家[EP]" AS epc_invalid
FROM derived_layer.report_patent_base
"""

INSERT_COUNTRY_SQL = """
INSERT INTO derived_layer.report_family_country (
    family_id, country_code, direct_patent_count, via_ep_count,
    family_incomplete, is_surrogate_family
) VALUES (%s, %s, %s, %s, %s, %s)
"""

INSERT_QUALITY_SQL = """
INSERT INTO derived_layer.report_family_quality (
    family_id, is_surrogate_family, member_rows, expected_counts_raw,
    family_incomplete, incomplete_detail_json,
    unknown_status_count, pending_status_count,
    ep_in_transition_count, ep_missing_epc_count, non_country_row_count
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def refresh_report_family_country() -> dict[str, Any]:
    """讀 report_patent_base、展開家族×國家、重建兩張 derived 表。"""
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database refresh. Install psycopg[binary].") from exc

    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SOURCE_SQL)
            rows = cur.fetchall()

        result = build_family_country_dataset(rows)

        # TRUNCATE + INSERT 包在同一 transaction（psycopg 預設不 autocommit），
        # 中斷時整批回滾，不會留下半套結果。
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE derived_layer.report_family_country;")
            cur.execute("TRUNCATE TABLE derived_layer.report_family_quality;")
            cur.executemany(
                INSERT_COUNTRY_SQL,
                [
                    (
                        r.family_id,
                        r.country_code,
                        r.direct_patent_count,
                        r.via_ep_count,
                        r.family_incomplete,
                        r.is_surrogate_family,
                    )
                    for r in result.country_rows
                ],
            )
            cur.executemany(
                INSERT_QUALITY_SQL,
                [
                    (
                        q.family_id,
                        q.is_surrogate_family,
                        q.member_rows,
                        q.expected_counts_raw,
                        q.family_incomplete,
                        json.dumps(q.incomplete_detail, ensure_ascii=False),
                        q.unknown_status_count,
                        q.pending_status_count,
                        q.ep_in_transition_count,
                        q.ep_missing_epc_count,
                        q.non_country_row_count,
                    )
                    for q in result.quality_rows
                ],
            )
        conn.commit()

    summary = dict(result.summary)
    summary["status"] = "refreshed"
    summary["source_rows"] = len(rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh derived_layer.report_family_country / report_family_quality from report_patent_base."
    )
    parser.parse_args()
    summary = refresh_report_family_country()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
