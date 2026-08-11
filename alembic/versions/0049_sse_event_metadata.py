"""SSE run 事件補 metadata：run_type／workspace_id／event_id／completed_at

Revision ID: 0049_sse_event_metadata
Revises: 0048_topic_assignment_source
Create Date: 2026-08-11

complete-sse-data-refresh：前端要依 job type 刷新對應資料區塊，但 fd301dee99c3
的 `notify_run_change` 只送 run_id/status/progress/stage——收到事件無從判斷
該刷新誰。本版把函式換成帶完整 metadata 的版本：

- `run_type`／`workspace_id`：前端 JOB_REFRESH_TARGETS mapping 的輸入。
- `event_id`＝`run_id:status`：終結事件的去重鍵（進度事件不去重，見 design.md）。
- `completed_at`：僅終結狀態（succeeded/failed/cancelled）帶，進度事件為 NULL。

⚠ 只 CREATE OR REPLACE FUNCTION，不動 trigger——`trg_workflow_runs_notify`
仍指向同名函式，replace 即生效。downgrade 還原 fd301 版函式本體（不得 DROP：
trigger 還掛著，DROP 後所有 workflow_runs UPDATE 都會炸）。

⚠ 發布時機不變：pg_notify 於 COMMIT 遞送（PostgreSQL 語意），
「succeeded 只在 persistence 成功後發布」由此天然成立。
"""
from __future__ import annotations

from alembic import op


revision = "0049_sse_event_metadata"
down_revision = "0048_topic_assignment_source"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION app_layer.notify_run_change()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status IS DISTINCT FROM NEW.status
               OR (OLD.worker_state_json->>'progress_percent')::int
                  IS DISTINCT FROM (NEW.worker_state_json->>'progress_percent')::int
            THEN
                PERFORM pg_notify('patent_events',
                    json_build_object(
                        'kind', 'run',
                        'event_id', NEW.run_id || ':' || NEW.status,
                        'run_id', NEW.run_id,
                        'run_type', NEW.run_type,
                        'workspace_id', NEW.workspace_id,
                        'status', NEW.status,
                        'progress', (NEW.worker_state_json->>'progress_percent')::int,
                        'stage', NEW.worker_state_json->>'current_stage',
                        'completed_at', CASE
                            WHEN NEW.status IN ('succeeded', 'failed', 'cancelled')
                            THEN NEW.worker_state_json->>'finished_at'
                            ELSE NULL
                        END
                    )::text
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    # 還原 fd301dee99c3 版本體（欄位集合與其 upgrade 完全一致）。
    op.execute("""
        CREATE OR REPLACE FUNCTION app_layer.notify_run_change()
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
