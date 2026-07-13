from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from backend.app.importers.wips_importer import canonicalize_record, first_parsed_date
from backend.app.mappings.wips import PUBLICATION_DATE_FIELDS
from backend.app.transforms.dates import year_from_date


SELECT_SQL = """
SELECT DISTINCT ON (p.id)
    p.id,
    rr.raw_data
FROM core_layer.patents p
JOIN core_layer.patent_sources ps ON ps.patent_id = p.id
JOIN raw_layer.raw_records rr ON rr.id = ps.raw_record_id
WHERE p.publication_date IS NULL
   OR p.publication_year IS NULL
ORDER BY p.id, ps.id DESC
"""

UPDATE_SQL = """
UPDATE core_layer.patents
SET
    publication_date = COALESCE(publication_date, %(publication_date)s),
    publication_year = COALESCE(publication_year, %(publication_year)s)
WHERE id = %(patent_id)s
  AND (publication_date IS NULL OR publication_year IS NULL)
"""


def backfill_publication_dates(dry_run: bool = False) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database maintenance. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    scanned = 0
    update_candidates = 0
    updated = 0
    with psycopg.connect(**get_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_SQL)
            rows = cur.fetchall()
            scanned = len(rows)
            for patent_id, raw_data in rows:
                canonical_raw = canonicalize_record(raw_data or {})
                publication_date = first_parsed_date(canonical_raw, PUBLICATION_DATE_FIELDS)
                if not publication_date:
                    continue
                update_candidates += 1
                if dry_run:
                    continue
                cur.execute(
                    UPDATE_SQL,
                    {
                        "patent_id": patent_id,
                        "publication_date": publication_date,
                        "publication_year": year_from_date(publication_date),
                    },
                )
                updated += cur.rowcount
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return {
        "status": "dry_run" if dry_run else "updated",
        "scanned_patents": scanned,
        "update_candidates": update_candidates,
        "updated_patents": updated,
        "publication_date_fields": PUBLICATION_DATE_FIELDS,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Backfill core_layer.patents publication dates from raw WIPS records.")
    parser.add_argument("--dry-run", action="store_true", help="Report candidates without updating the database.")
    args = parser.parse_args()
    summary = backfill_publication_dates(dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
