from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.app.reports.report_definitions import (
    ALLOWED_FILTER_COLUMNS,
    REPORT_DEFINITIONS,
    ReportDefinition,
)


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def qualified_table_name(table_name: str) -> str:
    return ".".join(quote_ident(part) for part in table_name.split("."))


def output_alias(column: str) -> str:
    return column


def build_filter_clause(filters: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    if not filters:
        return "", {}

    clauses = []
    params: dict[str, Any] = {}
    index = 0
    for column, value in filters.items():
        if column not in ALLOWED_FILTER_COLUMNS:
            raise ValueError(f"Unsupported report filter column: {column}")
        column_sql = quote_ident(column)
        if isinstance(value, dict):
            if "from" in value:
                param_name = f"filter_{index}"
                index += 1
                clauses.append(f"{column_sql} >= %({param_name})s")
                params[param_name] = value["from"]
            if "to" in value:
                param_name = f"filter_{index}"
                index += 1
                clauses.append(f"{column_sql} <= %({param_name})s")
                params[param_name] = value["to"]
            if "values" in value:
                values = value["values"]
                if not isinstance(values, list):
                    raise ValueError(f"Filter values must be a list: {column}")
                param_name = f"filter_{index}"
                index += 1
                clauses.append(f"{column_sql} = ANY(%({param_name})s)")
                params[param_name] = values
            continue
        if isinstance(value, list):
            param_name = f"filter_{index}"
            index += 1
            clauses.append(f"{column_sql} = ANY(%({param_name})s)")
            params[param_name] = value
            continue
        param_name = f"filter_{index}"
        index += 1
        clauses.append(f"{column_sql} = %({param_name})s")
        params[param_name] = value

    return " AND ".join(clauses), params


def build_exclude_blank_clause(columns: tuple[str, ...]) -> str:
    clauses = [f"NULLIF(BTRIM({quote_ident(column)}::text), '') IS NOT NULL" for column in columns]
    return " AND ".join(clauses)


def build_order_clause(definition: ReportDefinition) -> str:
    if not definition.default_order:
        return ""
    parts = []
    allowed_outputs = {output_alias(column) for column in definition.columns}
    allowed_outputs.add("patent_count")
    for column, direction in definition.default_order:
        direction_sql = "DESC" if direction.lower() == "desc" else "ASC"
        if column in allowed_outputs:
            parts.append(f"{quote_ident(column)} {direction_sql}")
        else:
            parts.append(f"{quote_ident(output_alias(column))} {direction_sql}")
    return " ORDER BY " + ", ".join(parts)


def build_report_sql(
    definition: ReportDefinition,
    filters: dict[str, Any] | None,
    limit: int | None,
    patent_ids: list[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    filter_clause, params = build_filter_clause(filters)
    blank_clause = build_exclude_blank_clause(definition.exclude_blank_columns)
    patent_ids_clause = ""
    if patent_ids is not None:
        # Restrict to an explicit patent set snapshot (used by app_layer analyses).
        patent_ids_clause = "patent_id = ANY(%(patent_ids)s)"
        params["patent_ids"] = list(patent_ids)
    where_parts = [part for part in (filter_clause, blank_clause, patent_ids_clause) if part]
    where_sql = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    table_sql = qualified_table_name(definition.source_table)

    if definition.report_type == "aggregate":
        select_columns = ", ".join(
            f"{quote_ident(column)} AS {quote_ident(output_alias(column))}" for column in definition.group_by
        )
        group_columns = ", ".join(quote_ident(column) for column in definition.group_by)
        sql = (
            f"SELECT {select_columns}, COUNT({quote_ident(definition.count_column)})::int AS patent_count "
            f"FROM {table_sql}"
            f"{where_sql} "
            f"GROUP BY {group_columns}"
            f"{build_order_clause(definition)}"
        )
    elif definition.report_type == "detail":
        select_columns = ", ".join(
            f"{quote_ident(column)} AS {quote_ident(output_alias(column))}" for column in definition.columns
        )
        sql = f"SELECT {select_columns} FROM {table_sql}{where_sql}{build_order_clause(definition)}"
    else:
        raise ValueError(f"Unsupported report type: {definition.report_type}")

    effective_limit = limit if limit is not None else definition.default_limit
    if effective_limit is not None:
        params["limit"] = int(effective_limit)
        sql += " LIMIT %(limit)s"
    return sql, params


def run_report(
    report_name: str,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    patent_ids: list[Any] | None = None,
) -> dict[str, Any]:
    definition = REPORT_DEFINITIONS.get(report_name)
    if not definition:
        raise ValueError(f"Unknown report: {report_name}")

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("psycopg is required for report execution. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    sql, params = build_report_sql(definition, filters, limit, patent_ids)
    with psycopg.connect(**get_connection_kwargs(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return {
        "report_name": definition.name,
        "label": definition.label,
        "report_type": definition.report_type,
        "filters": filters or {},
        "row_count": len(rows),
        "rows": rows,
    }


def parse_json_arg(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--filters must be a JSON object")
    return parsed


def load_filters(filters: str | None, filters_file: Path | None) -> dict[str, Any] | None:
    if filters and filters_file:
        raise ValueError("Use either --filters or --filters-file, not both.")
    if filters_file:
        return parse_json_arg(filters_file.read_text(encoding="utf-8-sig"))
    return parse_json_arg(filters)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run report definitions against derived_layer.report_patent_base.")
    parser.add_argument("report_name", choices=sorted(REPORT_DEFINITIONS), help="Report definition name.")
    parser.add_argument("--filters", help="JSON object for supported report filters.")
    parser.add_argument("--filters-file", type=Path, help="Path to a UTF-8 JSON file for supported report filters.")
    parser.add_argument("--limit", type=int, help="Override report limit.")
    args = parser.parse_args()

    result = run_report(args.report_name, filters=load_filters(args.filters, args.filters_file), limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
