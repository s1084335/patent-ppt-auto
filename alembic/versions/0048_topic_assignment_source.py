"""topic_assignments 加 assigned_source 來源標記。

Revision ID: 0048_topic_assignment_source

openspec change add-technical-channel-ai-backfill（2026-08-07 使用者確認規格）：
批次核准的 AI 補分指派要與幾何分群指派可區分（報表母體註記分計）。
既有列由 server_default 'geometric' 吸收，資料零改動。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_topic_assignment_source"
down_revision = "0047_tw_legal_status_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "topic_assignments",
        sa.Column(
            "assigned_source",
            sa.Text(),
            server_default=sa.text("'geometric'"),
            nullable=False,
        ),
        schema="derived_layer",
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.topic_assignments.assigned_source "
        "IS 'geometric=幾何分群指派；ai_backfill_approved=AI 建議經人工批次核准寫入'"
    )


def downgrade() -> None:
    op.drop_column("topic_assignments", "assigned_source", schema="derived_layer")
