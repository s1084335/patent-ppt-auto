"""promote examined publication number to patent identifier

Revision ID: 0002_examined_pub_id
Revises: 0001_baseline_schema
Create Date: 2026-07-13
"""
from __future__ import annotations

from alembic import op

revision = "0002_examined_pub_id"
down_revision = "0001_baseline_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE core_layer.patents
            ADD COLUMN IF NOT EXISTS "審查的公告號" TEXT;
        """
    )
    op.execute(
        """
        UPDATE core_layer.patents p
        SET "審查的公告號" = source_attr."審查的公告號"
        FROM (
            SELECT DISTINCT ON (patent_id)
                patent_id,
                "審查的公告號"
            FROM core_layer.patent_attributes
            WHERE NULLIF(BTRIM("審查的公告號"), '') IS NOT NULL
            ORDER BY patent_id, id DESC
        ) source_attr
        WHERE p.id = source_attr.patent_id
          AND p."審查的公告號" IS NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE core_layer.patent_attributes
            DROP COLUMN IF EXISTS "審查的公告號";
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_patents_examined_publication_number
            ON core_layer.patents("審查的公告號");
        """
    )
    op.execute(
        """
        ALTER TABLE derived_layer.report_patent_base
            ADD COLUMN IF NOT EXISTS "審查的公告號" TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS core_layer.idx_patents_examined_publication_number;
        """
    )
    op.execute(
        """
        ALTER TABLE derived_layer.report_patent_base
            DROP COLUMN IF EXISTS "審查的公告號";
        """
    )
    op.execute(
        """
        ALTER TABLE core_layer.patent_attributes
            ADD COLUMN IF NOT EXISTS "審查的公告號" TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE core_layer.patents
            DROP COLUMN IF EXISTS "審查的公告號";
        """
    )
