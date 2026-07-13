"""reorder patent number columns

Revision ID: 0003_reorder_patent_cols
Revises: 0002_examined_pub_id
Create Date: 2026-07-13
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0003_reorder_patent_cols"
down_revision = "0002_examined_pub_id"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).parents[2] / "sql" / "014_reorder_patent_number_columns.sql"


def upgrade() -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
    if sql.lstrip().upper().startswith("BEGIN;"):
        sql = sql.lstrip()[len("BEGIN;") :]
    if sql.rstrip().upper().endswith("COMMIT;"):
        sql = sql.rstrip()[: -len("COMMIT;")]
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("Column-order migration is not reversible without rebuilding the previous table order.")
