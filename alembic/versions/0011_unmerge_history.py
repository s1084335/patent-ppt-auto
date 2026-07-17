"""add versioned unmerge audit state

Revision ID: 0011_unmerge_history
Revises: 0010_workspace_topic_engine
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0011_unmerge_history"
down_revision = "0010_workspace_topic_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """加入可依 merge run 獨立復原所需的 run 與 topic 稽核狀態。"""
    op.execute(
        "ALTER TABLE derived_layer.topic_runs "
        "DROP CONSTRAINT topic_runs_run_mode_check;"
    )
    op.execute(
        "ALTER TABLE derived_layer.topic_runs "
        "ADD CONSTRAINT topic_runs_run_mode_check "
        "CHECK (run_mode = ANY (ARRAY["
        "'full'::text, 'incremental'::text, 'merge'::text, 'unmerge'::text]));"
    )
    op.execute(
        """
        ALTER TABLE derived_layer.topic_runs
            ADD COLUMN reverted_at TIMESTAMPTZ,
            ADD COLUMN reverted_by TEXT,
            ADD COLUMN reverted_by_run_id BIGINT,
            ADD CONSTRAINT topic_runs_reverted_by_run_id_fkey
                FOREIGN KEY (reverted_by_run_id)
                REFERENCES derived_layer.topic_runs(run_id)
                ON DELETE RESTRICT,
            ADD CONSTRAINT topic_runs_revert_state_check CHECK (
                (reverted_at IS NULL AND reverted_by IS NULL AND reverted_by_run_id IS NULL)
                OR
                (reverted_at IS NOT NULL AND reverted_by IS NOT NULL AND reverted_by_run_id IS NOT NULL)
            );
        """
    )

    op.execute(
        "ALTER TABLE derived_layer.topics "
        "DROP CONSTRAINT topics_merge_state_check;"
    )
    op.execute(
        "ALTER TABLE derived_layer.topics "
        "DROP CONSTRAINT topics_status_check;"
    )
    op.execute(
        """
        ALTER TABLE derived_layer.topics
            ADD COLUMN reverted_by TEXT,
            ADD COLUMN reverted_at TIMESTAMPTZ,
            ADD CONSTRAINT topics_status_check
                CHECK (status = ANY (ARRAY[
                    'active'::text,
                    'merged'::text,
                    'reverted'::text
                ])),
            ADD CONSTRAINT topics_merge_state_check CHECK (
                (
                    status = 'active'
                    AND merged_into_topic_id IS NULL
                    AND merged_at IS NULL
                    AND reverted_at IS NULL
                    AND reverted_by IS NULL
                )
                OR
                (
                    status = 'merged'
                    AND merged_into_topic_id IS NOT NULL
                    AND merged_at IS NOT NULL
                    AND reverted_at IS NULL
                    AND reverted_by IS NULL
                )
                OR
                (
                    status = 'reverted'
                    AND merged_into_topic_id IS NULL
                    AND merged_at IS NULL
                    AND reverted_at IS NOT NULL
                    AND reverted_by IS NOT NULL
                )
            );
        """
    )


def downgrade() -> None:
    """unmerge 會改變 artifact 歷史語義，需以 migration 前備份還原。"""
    raise RuntimeError(
        "0011 adds unmerge audit history; restore the pre-0011 database backup instead"
    )
