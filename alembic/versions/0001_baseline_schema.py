"""baseline schema (raw / core / derived / app layers)

Consolidated baseline captured from the working dev database (sql/001-012).
A fresh database reaches the full four-layer schema via `alembic upgrade head`;
an existing database that already has this schema is marked with
`alembic stamp head` instead of re-running it.

The DDL lives in the sibling 0001_baseline_schema.sql (pg_dump --schema-only,
cleaned of psql meta-commands) so the large statement set stays readable.

Revision ID: 0001_baseline_schema
Revises:
Create Date: 2026-07-07
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_baseline_schema"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ("app_layer", "derived_layer", "core_layer", "raw_layer")
BASELINE_SQL = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    sql = BASELINE_SQL.read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP SCHEMA IF EXISTS " + ", ".join(SCHEMAS) + " CASCADE;"
    )
