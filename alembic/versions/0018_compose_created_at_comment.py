"""add workspace compose source created_at comment

Revision ID: 0018_compose_created_at_comment
Revises: 0017_compose_composite_pk
Create Date: 2026-07-19

只補 app_layer.workspace_compose_sources.created_at 欄位註解，不修改既有 schema。
"""
from __future__ import annotations

from alembic import op


revision = "0018_compose_created_at_comment"
down_revision = "0017_compose_composite_pk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """補上來源關聯紀錄建立時間欄位的 PostgreSQL comment。"""
    op.execute(
        "COMMENT ON COLUMN app_layer.workspace_compose_sources.created_at IS "
        "'來源關聯紀錄建立時間';"
    )


def downgrade() -> None:
    """移除來源關聯紀錄建立時間欄位的 PostgreSQL comment。"""
    op.execute(
        "COMMENT ON COLUMN app_layer.workspace_compose_sources.created_at IS NULL;"
    )
