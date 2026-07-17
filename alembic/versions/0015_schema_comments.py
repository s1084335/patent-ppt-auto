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

from alembic import op

from backend.app.db.schema_comments import emit, emit_clear


revision = "0015_schema_comments"
down_revision = "0014_drop_analysis_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """對 PG 套用所有表/欄註解（內容來自 schema_comments.COMMENTS）。"""
    for statement in emit("postgresql"):
        op.execute(statement)


def downgrade() -> None:
    """移除本 migration 加入的所有註解（設回 NULL）。"""
    for statement in emit_clear("postgresql"):
        op.execute(statement)
