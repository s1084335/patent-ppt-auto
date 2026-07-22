"""SSE notify triggers: workflow_runs AFTER UPDATE + workflow_outputs AFTER INSERT → pg_notify('patent_events')

Revision ID: fd301dee99c3
Revises: 0021_derived_app_consolidation
Create Date: 2026-07-21
"""
from __future__ import annotations

from alembic import op


revision = "fd301dee99c3"
down_revision = "0021_derived_app_consolidation"
branch_labels = None
depends_on = None

_TRIGGER_RUN = "trg_workflow_runs_notify"
_TRIGGER_OUTPUT = "trg_workflow_outputs_notify"
_FN_RUN = "notify_run_change"
_FN_OUTPUT = "notify_output_insert"


def upgrade() -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION app_layer.{_FN_RUN}()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status IS DISTINCT FROM NEW.status
               OR (OLD.worker_state_json->>'progress_percent')::int
                  IS DISTINCT FROM (NEW.worker_state_json->>'progress_percent')::int
            THEN
                PERFORM pg_notify('patent_events',
                    json_build_object(
                        'kind', 'run',
                        'run_id', NEW.run_id,
                        'status', NEW.status,
                        'progress', (NEW.worker_state_json->>'progress_percent')::int,
                        'stage', NEW.worker_state_json->>'current_stage'
                    )::text
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION app_layer.{_FN_OUTPUT}()
        RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify('patent_events',
                json_build_object(
                    'kind', 'output',
                    'run_id', NEW.run_id,
                    'output_type', NEW.output_type,
                    'version', NEW.version
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER_RUN}
        AFTER UPDATE ON app_layer.workflow_runs
        FOR EACH ROW
        EXECUTE FUNCTION app_layer.{_FN_RUN}()
    """)
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER_OUTPUT}
        AFTER INSERT ON app_layer.workflow_outputs
        FOR EACH ROW
        EXECUTE FUNCTION app_layer.{_FN_OUTPUT}()
    """)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_RUN} ON app_layer.workflow_runs")
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_OUTPUT} ON app_layer.workflow_outputs")
    op.execute(f"DROP FUNCTION IF EXISTS app_layer.{_FN_RUN}()")
    op.execute(f"DROP FUNCTION IF EXISTS app_layer.{_FN_OUTPUT}()")
