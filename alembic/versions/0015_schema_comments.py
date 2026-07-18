"""apply schema comments (app+derived columns, core/raw table-level)

Revision ID: 0015_schema_comments
Revises: 0014_drop_analysis_results
Create Date: 2026-07-17

把 backend.app.db.schema_comments.COMMENTS（dialect 中立唯一來源）套進 PG：
本 migration 只對 PostgreSQL 發 COMMENT ON。最終目的地 SQL Server 移植時，
另以同一份 COMMENTS 呼叫 emit("mssql") 產生 extended property，內容不變。
註解只是 metadata，不新增表、不改結構與資料。
"""
from __future__ import annotations

from alembic import context, op

from backend.app.db.schema_comments import emit, emit_clear


revision = "0015_schema_comments"
down_revision = "0014_drop_analysis_results"
branch_labels = None
depends_on = None


def _existing_object_columns() -> dict[str, set[str]]:
    """回傳目前 DB 內各 schema.table → 欄位集合（含 view，view 欄位也在
    information_schema.columns）。用來把 COMMENTS 過濾成「此 migration 時點
    確實存在」的物件，避免引用未來 migration 才建立的 table/column 而失敗。
    """
    rows = op.get_bind().exec_driver_sql(
        "SELECT table_schema, table_name, column_name "
        "FROM information_schema.columns "
        "WHERE table_schema IN ('app_layer','derived_layer','core_layer','raw_layer')"
    ).fetchall()
    existing: dict[str, set[str]] = {}
    for schema, table, column in rows:
        existing.setdefault(f"{schema}.{table}", set()).add(column)
    return existing


def _apply(statements_fn) -> None:
    """套用註解 DDL。線上模式只對已存在物件下註解；離線 SQL 產生模式無法內省，
    退回產出全部（以撰寫當時 schema 為準）。"""
    if context.is_offline_mode():
        for statement in statements_fn(include=None):
            op.execute(statement)
        return
    existing = _existing_object_columns()

    def include(qualified: str, column: str | None) -> bool:
        columns = existing.get(qualified)
        if columns is None:
            return False
        return column is None or column in columns

    for statement in statements_fn(include=include):
        op.execute(statement)


def upgrade() -> None:
    """對 PG 套用表/欄註解，僅限此時點已存在的物件（內容來自 COMMENTS）。"""
    _apply(lambda *, include: emit("postgresql", include=include))


def downgrade() -> None:
    """移除本 migration 加入的註解（設回 NULL），僅限已存在物件。"""
    _apply(lambda *, include: emit_clear("postgresql", include=include))
