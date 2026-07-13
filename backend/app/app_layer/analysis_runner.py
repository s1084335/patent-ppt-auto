"""App-layer analysis runner (CLI-first).

Two subcommands drive the app_layer traceability chain:

  create-analysis   snapshot a patent set into app_layer.analysis_runs
  run-reports       run the report definitions over that snapshot and store
                    each result into app_layer.analysis_outputs

The runner never touches raw/core/derived data; it only reads
derived_layer.report_patent_base and writes app_layer tables. analysis_id is the
shared trace key. Failure reason is recorded only on analysis_runs.error_message.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.app.reports.report_definitions import REPORT_DEFINITIONS, REPORT_SOURCE_TABLE
from backend.app.reports.report_engine import (
    build_filter_clause,
    qualified_table_name,
    run_report,
)


def _connect():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("psycopg is required. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    return psycopg.connect(**get_connection_kwargs(), row_factory=dict_row)


def _jsonb(value: Any):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def load_json_file(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return parsed


def query_patent_ids(filters: dict[str, Any] | None) -> list[int]:
    """Return the patent_id set from report_patent_base matching the filters."""
    where_clause, params = build_filter_clause(filters)
    where_sql = f" WHERE {where_clause}" if where_clause else ""
    sql = (
        f"SELECT patent_id FROM {qualified_table_name(REPORT_SOURCE_TABLE)}"
        f"{where_sql} ORDER BY patent_id"
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [row["patent_id"] for row in cur.fetchall()]


def create_analysis(
    name: str,
    analysis_type: str,
    filters: dict[str, Any] | None,
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    patent_ids = query_patent_ids(filters)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_layer.analysis_runs
                    (analysis_name, analysis_type, status,
                     filter_json, parameters_json, selected_patent_ids_json)
                VALUES (%s, %s, 'pending', %s, %s, %s)
                RETURNING analysis_id
                """,
                (
                    name,
                    analysis_type,
                    _jsonb(filters or {}),
                    _jsonb(parameters or {}),
                    _jsonb(patent_ids),
                ),
            )
            analysis_id = cur.fetchone()["analysis_id"]
        conn.commit()
    return {"analysis_id": analysis_id, "patent_count": len(patent_ids)}


def _fetch_analysis(cur, analysis_id: int) -> dict[str, Any] | None:
    cur.execute(
        "SELECT analysis_id, status, selected_patent_ids_json "
        "FROM app_layer.analysis_runs WHERE analysis_id = %s",
        (analysis_id,),
    )
    return cur.fetchone()


def run_reports(analysis_id: int) -> dict[str, Any]:
    with _connect() as conn:
        with conn.cursor() as cur:
            row = _fetch_analysis(cur, analysis_id)
            if row is None:
                raise ValueError(f"analysis_id {analysis_id} not found")

            patent_ids = list(row["selected_patent_ids_json"] or [])
            cur.execute(
                "UPDATE app_layer.analysis_runs SET status = 'running' WHERE analysis_id = %s",
                (analysis_id,),
            )
            conn.commit()

            output_count = 0
            try:
                for report_name in sorted(REPORT_DEFINITIONS):
                    result = run_report(report_name, patent_ids=patent_ids)
                    cur.execute(
                        """
                        INSERT INTO app_layer.analysis_outputs
                            (analysis_id, output_type, output_name, result_json)
                        VALUES (%s, 'chart_data', %s, %s)
                        """,
                        (analysis_id, report_name, _jsonb(result["rows"])),
                    )
                    conn.commit()  # commit per output so partial results survive a later failure
                    output_count += 1
                cur.execute(
                    "UPDATE app_layer.analysis_runs "
                    "SET status = 'completed', completed_at = now(), error_message = NULL "
                    "WHERE analysis_id = %s",
                    (analysis_id,),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - record failure, keep committed outputs
                conn.rollback()  # clear any aborted transaction before recording failure
                cur.execute(
                    "UPDATE app_layer.analysis_runs "
                    "SET status = 'failed', error_message = %s WHERE analysis_id = %s",
                    (f"{type(exc).__name__}: {exc}", analysis_id),
                )
                conn.commit()
                raise

    return {"analysis_id": analysis_id, "status": "completed", "output_count": output_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="App-layer analysis runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-analysis", help="Snapshot a patent set into analysis_runs.")
    create.add_argument("--name", required=True, help="Analysis name.")
    create.add_argument("--type", dest="analysis_type", default="report",
                        help="Analysis type (report / infringement / patentability).")
    create.add_argument("--filters-file", type=Path, help="UTF-8 JSON file of report filters.")
    create.add_argument("--parameters-file", type=Path, help="UTF-8 JSON file of run parameters.")

    run = sub.add_parser("run-reports", help="Run report definitions over an analysis snapshot.")
    run.add_argument("analysis_id", type=int, help="Existing analysis_id.")

    args = parser.parse_args()

    try:
        if args.command == "create-analysis":
            result = create_analysis(
                args.name,
                args.analysis_type,
                load_json_file(args.filters_file),
                load_json_file(args.parameters_file),
            )
        elif args.command == "run-reports":
            result = run_reports(args.analysis_id)
        else:  # pragma: no cover - argparse enforces required subcommand
            parser.error("unknown command")
    except Exception as exc:  # noqa: BLE001 - CLI boundary: emit a clean error, exit non-zero
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
