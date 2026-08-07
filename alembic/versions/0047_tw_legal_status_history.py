"""add TW legal status history column

Revision ID: 0047_tw_legal_status_history
Revises: 0046_core_field_reclassification
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0047_tw_legal_status_history"
down_revision = "0046_core_field_reclassification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "patents",
        sa.Column(
            "legal_status_history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema="core_layer",
    )
    op.execute(
        "COMMENT ON COLUMN core_layer.patents.legal_status_history "
        "IS 'TW manual legal_status registration history: from_status, to_status, changed_at.'"
    )


def downgrade() -> None:
    op.drop_column("patents", "legal_status_history", schema="core_layer")
