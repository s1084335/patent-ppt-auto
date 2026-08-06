from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.app.reports.report_definitions import (
    ALLOWED_FILTER_COLUMNS,
    REPORT_DEFINITIONS,
    ReportDefinition,
    allowed_filter_columns_for_report,
)


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


# aggregate 型報表可用的額外聚合函式白名單（ReportDefinition.aggregates 的第一欄）。
# value 是 SQL 模板：{col} 會代入 quote 過的來源欄名。
AGGREGATE_FUNCTIONS = {
    "sum": "COALESCE(SUM({col}), 0)::bigint",
    "count": "COUNT({col})::int",  # 非空列數：用來區分「彙總=0」與「根本無資料」
    "count_nonblank": "COUNT(*) FILTER (WHERE NULLIF(BTRIM({col}::text), '') IS NOT NULL)::int",
    "string_agg_distinct_nonblank": "COALESCE(STRING_AGG(DISTINCT NULLIF(BTRIM({col}::text), ''), '; ' ORDER BY NULLIF(BTRIM({col}::text), '')) FILTER (WHERE NULLIF(BTRIM({col}::text), '') IS NOT NULL), '')",
    "count_distinct": "COUNT(DISTINCT {col})::int",
    "avg": "AVG({col})::numeric(12,2)",
    "max": "MAX({col})",
    # _excl_group 變體：聚合欄與分組鍵（group_by 第一欄）同值時不計——
    # 用於「申請人＝最新受讓人」這種未離手情況不算轉讓（通用比對，不寫死欄名）。
    "count_nonblank_excl_group": (
        "COUNT(*) FILTER (WHERE NULLIF(BTRIM({col}::text), '') IS NOT NULL "
        "AND NULLIF(BTRIM({col}::text), '') IS DISTINCT FROM NULLIF(BTRIM({group_col}::text), ''))::int"
    ),
    # 反向計數（2026-07-29「受讓取得」欄）：算**有多少列的 {col} 等於本組分組鍵**。
    # 與 _excl_group 變體方向相反——那些查「自己這列的欄位」，這個查「別人指向我」。
    # 用途：YIXUAN 那列要顯示「有 2 筆專利的最新受讓人是 YIXUAN」，
    # 而那 2 筆的申請人是 MARIO，不在 YIXUAN 自己的分組裡。
    # ⚠ 相關子查詢每組執行一次；60 筆資料無感，上萬筆需改 LEFT JOIN 預聚合。
    "count_as_value_of": (
        # ⚠ 外層分組欄必須用 {table}.{group_col} 完整限定：子查詢裡有 _rev 別名，
        # 裸欄名會被 PostgreSQL 解析成 _rev 自己的欄位（最內層作用域優先），
        # 相關子查詢退化成無關聯常數——實測所有 37 列都回同一個數字 2。
        "(SELECT COUNT(*) FROM {table} _rev "
        "WHERE NULLIF(BTRIM(_rev.{col}::text), '') "
        "IS NOT DISTINCT FROM NULLIF(BTRIM({table}.{group_col}::text), ''))::int"
    ),
    # ── #3 申請結構（2026-08-05 定案）：兩個獨立屬性各自計數 ──
    # ⚠ 判定一律用**原始多值欄**（`A | B`）。不改 source_table 到展開 VIEW：
    # 那會讓件數重複計數（實測 60→74），違反「兩段加總＝總件數」。
    # ⚠ 判定對象是 COALESCE(主欄, 備援欄)——**必須與顯示名的推導同一套順位**。
    # 實測教訓（2026-08-05）：專利權人顯示名主要來自「最近專利權人」（36 筆非空、
    # 10 筆多值），而「標準當前專利權人」只有 3 筆非空且 0 筆多值；只看後者的話
    # 「共同持有」永遠是 0，功能靜默失效而且驗不出來。
    "count_multivalue": (
        "COUNT(*) FILTER (WHERE position('|' in "
        "COALESCE(NULLIF(BTRIM({col}::text), ''), {extra_col}::text, '')) > 0)::int"
    ),
    # 已轉讓沿用既有 _excl_group 口徑（受讓人＝自己不算轉讓），再依多值與否分流，
    # 讓「共同且已轉讓」與「單獨且已轉讓」各自可數——只有總數的話這兩者分不開。
    "count_multivalue_transferred": (
        "COUNT(*) FILTER (WHERE position('|' in COALESCE({col}::text, '')) > 0 "
        "AND NULLIF(BTRIM({extra_col}::text), '') IS NOT NULL "
        "AND NULLIF(BTRIM({extra_col}::text), '') "
        "IS DISTINCT FROM NULLIF(BTRIM({group_col}::text), ''))::int"
    ),
    "count_singlevalue_transferred": (
        "COUNT(*) FILTER (WHERE position('|' in COALESCE({col}::text, '')) = 0 "
        "AND NULLIF(BTRIM({extra_col}::text), '') IS NOT NULL "
        "AND NULLIF(BTRIM({extra_col}::text), '') "
        "IS DISTINCT FROM NULLIF(BTRIM({group_col}::text), ''))::int"
    ),
    # 共同者名單＝多值欄裡**除了分組鍵本人以外**的其他人。
    #
    # 🔴 2026-08-06 修正（Codex 驗收揪出）：原本用序位 `_x.ord > 1` 排除第 1 個，
    # 那是「分組鍵一定是第 1 個」的假設——在 **base 表**成立（`applicant_display_name`
    # ＝`split_part(申請人,'|',1)`），但三張申請人報表 2026-08-06 改讀**展開 VIEW** 後
    # **不成立**：展開後分組鍵可能是第 2 個。
    # 實例：`廈門帝瑪斯 | 曾晴` 這件在「曾晴」那一列，`ord > 1` 會留下**曾晴自己**、
    # 反而漏掉真正的夥伴帝瑪斯——欄位語意整個反過來，而且不會報錯。
    #
    # ⚠ 比對必須用**收斂後**的名字：分組鍵是走過 `company_aliases` 的中文顯示名，
    # 多值欄拆出來的是英文原字面，直接比字面永遠不相等 → 本人不會被排除。
    # 故 `_ca` 收斂結果同時用於輸出與比對，兩邊同一套（一邊中文一邊英文會被讀成兩家公司）。
    "string_agg_co_values": (
        "COALESCE((SELECT STRING_AGG(DISTINCT COALESCE("
        "NULLIF(BTRIM(_ca.\"公司中文名稱\"), ''), NULLIF(BTRIM(_ca.\"正規化名稱\"), ''), _x.part"
        "), '; ') FROM UNNEST(ARRAY_AGG(COALESCE(NULLIF(BTRIM({col}::text), ''), {extra_col}::text))) AS _raw "
        "CROSS JOIN LATERAL (SELECT BTRIM(p) AS part, o AS ord FROM "
        "regexp_split_to_table(COALESCE(_raw, ''), '\\s*\\|\\s*') WITH ORDINALITY AS t(p, o)) _x "
        "LEFT JOIN LATERAL (SELECT c.\"公司中文名稱\", c.\"正規化名稱\" "
        "FROM derived_layer.company_aliases c WHERE c.review_status = 'confirmed' "
        "AND lower(regexp_replace(BTRIM(c.\"別稱\"), '\\s+', ' ', 'g')) "
        "= lower(regexp_replace(_x.part, '\\s+', ' ', 'g')) ORDER BY c.id LIMIT 1) _ca ON true "
        "WHERE NULLIF(_x.part, '') IS NOT NULL "
        "AND COALESCE(NULLIF(BTRIM(_ca.\"公司中文名稱\"), ''), "
        "NULLIF(BTRIM(_ca.\"正規化名稱\"), ''), _x.part) "
        "IS DISTINCT FROM NULLIF(BTRIM({group_col}::text), '')), '')"
    ),
    "string_agg_distinct_nonblank_excl_group": (
        "COALESCE(STRING_AGG(DISTINCT NULLIF(BTRIM({col}::text), ''), '; ' "
        "ORDER BY NULLIF(BTRIM({col}::text), '')) "
        "FILTER (WHERE NULLIF(BTRIM({col}::text), '') IS NOT NULL "
        "AND NULLIF(BTRIM({col}::text), '') IS DISTINCT FROM NULLIF(BTRIM({group_col}::text), '')), '')"
    ),
}


def build_aggregate_columns(definition: ReportDefinition) -> str:
    """把 definition.aggregates 組成 SELECT 片段（含前置逗號），無聚合時回空字串。"""
    parts: list[str] = []
    group_col = quote_ident(definition.group_by[0]) if definition.group_by else None
    for entry in definition.aggregates:
        # 第四元素（可選）＝第二個來源欄，供需要兩欄的聚合使用
        # （#3「共同且已轉讓」要同時看多值欄與受讓人欄）。
        # ⚠ 用可選第四元素而不是把欄名寫死進模板：寫死就等於為單一報表訂做，
        # 下一個報表要用就得再抄一份模板（本專案已因此犯過多次兩處落點）。
        func, column, alias = entry[0], entry[1], entry[2]
        extra_column = entry[3] if len(entry) > 3 else None
        template = AGGREGATE_FUNCTIONS.get(func)
        if template is None:
            raise ValueError(f"Unsupported aggregate function: {func} (report {definition.name})")
        if "{group_col}" in template and group_col is None:
            raise ValueError(f"Aggregate {func} requires group_by (report {definition.name})")
        # {table}＝來源表（反向子查詢用）。舊模板不含此佔位符，format 會忽略多餘參數。
        rendered = template.format(
            col=quote_ident(column),
            group_col=group_col,
            table=qualified_table_name(definition.source_table),
            # 未給第四元素時 extra_col 回落到 col 本身——模板裡的
            # COALESCE(主欄, 備援欄) 就退化成單欄，單欄與雙欄共用同一組模板。
            extra_col=quote_ident(extra_column) if extra_column else quote_ident(column),
        )
        parts.append(f"{rendered} AS {quote_ident(alias)}")
    return (", " + ", ".join(parts)) if parts else ""


def qualified_table_name(table_name: str) -> str:
    return ".".join(quote_ident(part) for part in table_name.split("."))


def output_alias(column: str) -> str:
    return column


def build_filter_clause(
    filters: dict[str, Any] | None,
    allowed_columns: set[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """依允許欄位建立 WHERE 條件；allowed_columns 未給時沿用全域白名單。"""
    if not filters:
        return "", {}

    usable_columns = allowed_columns or ALLOWED_FILTER_COLUMNS
    clauses = []
    params: dict[str, Any] = {}
    index = 0
    for column, value in filters.items():
        if column not in usable_columns:
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


def build_value_pattern_clause(
    pattern_columns: tuple[tuple[str, str], ...],
) -> tuple[str, dict[str, Any]]:
    """值格式白名單：只收符合正規式的列（例：IPC 欄排除洛迦諾分類）。

    正規式走參數綁定（非字串拼接），欄名走 quote_ident——兩者都不讓外部輸入進 SQL 結構。
    """
    clauses = []
    params: dict[str, Any] = {}
    for index, (column, pattern) in enumerate(pattern_columns):
        key = f"value_pattern_{index}"
        clauses.append(f"BTRIM({quote_ident(column)}::text) ~ %({key})s")
        params[key] = pattern
    return " AND ".join(clauses), params


def build_order_clause(definition: ReportDefinition) -> str:
    if not definition.default_order:
        return ""
    parts = []
    allowed_outputs = {output_alias(column) for column in definition.columns}
    allowed_outputs.add("patent_count")
    # ⚠ 聚合允許可選第四元素（第二來源欄）——固定長度解包會在加了第四元素的
    # 報表上炸掉。與 build_aggregate_columns 同一個取法（entry[2]），不另立解析。
    allowed_outputs.update(entry[2] for entry in definition.aggregates)
    for column, direction in definition.default_order:
        direction_sql = "DESC" if direction.lower() == "desc" else "ASC"
        if column in allowed_outputs:
            parts.append(f"{quote_ident(column)} {direction_sql}")
        else:
            parts.append(f"{quote_ident(output_alias(column))} {direction_sql}")
    return " ORDER BY " + ", ".join(parts)


# 家族層級報表的 patent→家族轉譯：家族 id 產生規則必須與 transforms/family_layout
# 一致——WIPS同族ID 去空白後為空 → 'P{patent_id}' 單件家族（_surrogate_family_id）。
# refresh 寫入家族兩表的 family_id 用同一規則，這裡以 SQL 復刻；兩邊同步由
# tests/test_report_engine_family.py 釘住。（BTRIM 只去半形空白，與 Python strip()
# 對罕見全形空白略有差異；同族ID 實際值為代碼字串，不受影響。）
FAMILY_SCOPE_SOURCE_TABLE = "derived_layer.report_patent_base"
FAMILY_ID_EXPRESSION = "COALESCE(NULLIF(BTRIM(\"WIPS同族ID\"::text), ''), 'P' || patent_id::text)"


def build_family_scope_clause(
    filters: dict[str, Any] | None,
    patent_ids: list[Any] | None,
) -> tuple[str, dict[str, Any]]:
    """把 patent 層 filters／快照轉譯成家族集合條件（家族層級報表用）。

    口徑＝「選中專利所屬的家族」的完整佈局：filters/patent_ids 先在
    report_patent_base 圈出家族集合，家族表再以 family_id IN (...) 過濾；
    選中家族的所有列（全體成員的貢獻）都保留，佈局不缺國。
    """
    filter_clause, params = build_filter_clause(filters)
    parts = [filter_clause] if filter_clause else []
    if patent_ids is not None:
        parts.append("patent_id = ANY(%(patent_ids)s)")
        params["patent_ids"] = list(patent_ids)
    where_sql = " WHERE " + " AND ".join(parts) if parts else ""
    subquery = (
        f"SELECT DISTINCT {FAMILY_ID_EXPRESSION} "
        f"FROM {qualified_table_name(FAMILY_SCOPE_SOURCE_TABLE)}{where_sql}"
    )
    return f'"family_id" IN ({subquery})', params


def build_report_sql(
    definition: ReportDefinition,
    filters: dict[str, Any] | None,
    limit: int | None,
    patent_ids: list[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    blank_clause = build_exclude_blank_clause(definition.exclude_blank_columns)
    pattern_clause, pattern_params = build_value_pattern_clause(
        getattr(definition, "value_pattern_columns", ()) or ()
    )
    if definition.supports_patent_ids:
        filter_clause, params = build_filter_clause(
            filters,
            allowed_filter_columns_for_report(definition),
        )
        patent_ids_clause = ""
        if patent_ids is not None:
            # Restrict to an explicit patent set snapshot (used by app_layer analyses).
            patent_ids_clause = "patent_id = ANY(%(patent_ids)s)"
            params["patent_ids"] = list(patent_ids)
        where_parts = [part for part in (filter_clause, blank_clause, pattern_clause,
                                         patent_ids_clause) if part]
    else:
        # 家族層級報表：來源表沒有 patent 層欄位，filters/快照轉譯成家族集合條件
        # （不帶篩選＝全庫，維持既有行為）。
        family_clause = ""
        params = {}
        if filters or patent_ids is not None:
            family_clause, params = build_family_scope_clause(filters, patent_ids)
        where_parts = [part for part in (family_clause, blank_clause, pattern_clause) if part]
    if pattern_clause:
        params.update(pattern_params)
    where_sql = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    table_sql = qualified_table_name(definition.source_table)

    if definition.report_type == "aggregate":
        select_columns = ", ".join(
            f"{quote_ident(column)} AS {quote_ident(output_alias(column))}" for column in definition.group_by
        )
        group_columns = ", ".join(quote_ident(column) for column in definition.group_by)
        sql = (
            f"SELECT {select_columns}, COUNT({quote_ident(definition.count_column)})::int AS patent_count"
            f"{build_aggregate_columns(definition)} "
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
    if definition.report_type == "cluster":
        # cluster 型報表不吃單表 SQL：資料源＝分群定案（topic_assignments→主題）JOIN 申請人，
        # 由 chart_runner 的 cluster_analytics section 吃注入的 cluster_data 出圖。此處 fail loud，
        # 讓 run_reports_batch 以 skipped_reason 現形，不誤把 cluster 名餵進 build_report_sql。
        raise ValueError(
            f"cluster report {report_name} is rendered from cluster_data, not single-table SQL"
        )

    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    sql, params = build_report_sql(definition, filters, limit, patent_ids)
    with psycopg.connect(**get_connection_kwargs(), connect_timeout=15) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return {
        "report_name": definition.name,
        "label": definition.label,
        "label_zh": definition.label_zh,
        "report_type": definition.report_type,
        "filters": filters or {},
        "row_count": len(rows),
        "rows": rows,
    }


def run_reports_batch(
    report_names: list[str],
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    patent_ids: list[Any] | None = None,
) -> dict[str, Any]:
    """一次執行多張報表——報表引擎的對外調用契約。

    這個簽名就是之後包裝（前端 API、MCP reporting tools）共用的入口：
        report_names  要跑的報表 key（REPORT_DEFINITIONS 的鍵）
        filters       資料範圍（ALLOWED_FILTER_COLUMNS 白名單）
        limit         各報表列數上限（None 用各報表預設）
        patent_ids    analysis 快照的專利集合（None＝不限）

    家族層級報表收到 filters/patent_ids 時由引擎轉譯成「選中專利所屬家族」的
    家族集合（完整佈局、含家族全體成員），並附 note 說明口徑（見
    build_family_scope_clause）。

    回傳 {report_name: {label_zh, label, report_type, rows, row_count} 或
          {skipped_reason}}，未知的報表名也以 skipped_reason 回報。
    """
    results: dict[str, Any] = {}
    for name in report_names:
        definition = REPORT_DEFINITIONS.get(name)
        if definition is None:
            results[name] = {"skipped_reason": f"unknown report: {name}"}
            continue
        note: str | None = None
        if (filters or patent_ids is not None) and not definition.supports_patent_ids:
            # 家族層級報表：filters/快照經引擎轉譯成家族集合，口徑以註記現形。
            note = "家族層級口徑：篩選／快照圈定家族集合，佈局計入家族全體成員（可能含篩選外的國家）"
        try:
            report = run_report(name, filters=filters or None, limit=limit, patent_ids=patent_ids)
        except ValueError as exc:
            results[name] = {"label_zh": definition.label_zh, "skipped_reason": str(exc)}
            continue
        results[name] = {
            "label_zh": report["label_zh"],
            "label": report["label"],
            "report_type": report["report_type"],
            "row_count": report["row_count"],
            "rows": report["rows"],
        }
        if note:
            results[name]["note"] = note
    return results


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
