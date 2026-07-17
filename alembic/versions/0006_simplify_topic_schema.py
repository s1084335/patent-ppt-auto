"""simplify topic modeling tables and patent embeddings

Revision ID: 0006_simplify_topics
Revises: 0005_family_country_layout
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0006_simplify_topics"
down_revision = "0005_family_country_layout"
branch_labels = None
depends_on = None

# 現有 511 筆向量由這份本機 PatentSBERTa 權重產生；migration 將其 hash 補成版本。
PATENT_SBERTA_WEIGHT_VERSION = "sha256:930ede681b57524638b5934abc9098988431b387c0e2674ce1859a4e427fd0a5"


def upgrade() -> None:
    """保留既有向量，精簡 embedding 欄位並把八張主題表收斂成四張。"""
    _simplify_patent_embeddings()
    _replace_empty_topic_tables()


def downgrade() -> None:
    """阻止無法無損還原的降版，需使用 migration 前 full backup 復原。"""
    raise RuntimeError(
        "0006 merges topic tables and metadata columns and cannot be losslessly downgraded; "
        "restore the pre-0006 full database backup instead"
    )


def _simplify_patent_embeddings() -> None:
    """將舊 embedding 稽核欄位合併進 metadata_json，向量本體不重算。"""
    op.execute("DROP INDEX IF EXISTS core_layer.ux_patent_embeddings_identity;")
    op.execute("DROP INDEX IF EXISTS core_layer.idx_patent_embeddings_model_hash;")
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "DROP CONSTRAINT IF EXISTS patent_embeddings_vector_dim_check;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "DROP CONSTRAINT IF EXISTS patent_embeddings_core_patent_id_fkey;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "RENAME COLUMN core_patent_id TO patent_id;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "RENAME COLUMN text_cleaning_version TO preprocessing_version;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "RENAME COLUMN model_text_hash TO text_hash;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "ADD COLUMN model_version TEXT, "
        "ADD COLUMN metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;"
    )
    op.execute(
        f"""
        UPDATE core_layer.patent_embeddings
        SET
            model_version = '{PATENT_SBERTA_WEIGHT_VERSION}',
            metadata_json = jsonb_strip_nulls(
                COALESCE(preprocessing_metadata_json, '{{}}'::jsonb)
                || jsonb_build_object(
                    'raw_text_hash', raw_text_hash,
                    'tokenizer_name', tokenizer_name,
                    'max_seq_length', max_seq_length,
                    'vector_dim', vector_dim,
                    'vector_hash', vector_hash,
                    'chunk_token_counts', chunk_token_counts_json,
                    'chunk_weights', chunk_weights_json,
                    'model', model_metadata_json
                )
            )
        WHERE model_version IS NULL;
        """
    )
    op.execute(
        "ALTER TABLE core_layer.patent_embeddings "
        "ALTER COLUMN patent_id SET NOT NULL, "
        "ALTER COLUMN model_version SET NOT NULL;"
    )
    op.execute(
        """
        ALTER TABLE core_layer.patent_embeddings
            DROP COLUMN "授權公告號",
            DROP COLUMN "審查的公告號",
            DROP COLUMN "未審查的公開號",
            DROP COLUMN "申請號",
            DROP COLUMN raw_text_hash,
            DROP COLUMN tokenizer_name,
            DROP COLUMN max_seq_length,
            DROP COLUMN vector_dim,
            DROP COLUMN vector_hash,
            DROP COLUMN chunk_token_counts_json,
            DROP COLUMN chunk_weights_json,
            DROP COLUMN preprocessing_metadata_json,
            DROP COLUMN model_metadata_json;
        """
    )
    op.execute(
        """
        ALTER TABLE core_layer.patent_embeddings
            ADD CONSTRAINT patent_embeddings_patent_id_fkey
                FOREIGN KEY (patent_id)
                REFERENCES core_layer.patents(id)
                ON DELETE CASCADE;
        """
    )
    op.execute("DROP INDEX IF EXISTS core_layer.idx_patent_embeddings_core_patent_id;")
    op.execute(
        "CREATE INDEX idx_patent_embeddings_patent_id "
        "ON core_layer.patent_embeddings(patent_id);"
    )
    op.execute(
        "CREATE INDEX idx_patent_embeddings_lookup "
        "ON core_layer.patent_embeddings(source_field, embedding_model, model_version, text_hash);"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_patent_embeddings_identity
        ON core_layer.patent_embeddings(
            patent_id,
            source_field,
            embedding_model,
            model_version,
            preprocessing_version,
            text_hash,
            aggregation_method
        );
        """
    )


def _replace_empty_topic_tables() -> None:
    """確認舊表無結果後，建立 run、topic、assignment、candidate 四張正式表。"""
    # 目前尚未產生正式分群結果；若其他環境已有值，migration 必須中止並先設計轉檔。
    op.execute(
        """
        DO $$
        DECLARE
            populated_rows BIGINT;
        BEGIN
            SELECT
                (SELECT count(*) FROM derived_layer.topic_model_profiles)
                + (SELECT count(*) FROM derived_layer.topic_runs)
                + (SELECT count(*) FROM derived_layer.topic_model_artifacts)
                + (SELECT count(*) FROM derived_layer.topics)
                + (SELECT count(*) FROM derived_layer.topic_quality_metrics)
                + (SELECT count(*) FROM derived_layer.topic_candidate_selections)
                + (SELECT count(*) FROM derived_layer.topic_assignments)
                + (SELECT count(*) FROM derived_layer.topic_labels)
            INTO populated_rows;

            IF populated_rows > 0 THEN
                RAISE EXCEPTION
                    '0006 topic schema simplification requires empty legacy topic tables; found % rows',
                    populated_rows;
            END IF;
        END $$;
        """
    )
    op.execute("DROP TABLE derived_layer.topic_labels CASCADE;")
    op.execute("DROP TABLE derived_layer.topic_assignments CASCADE;")
    op.execute("DROP TABLE derived_layer.topic_candidate_selections CASCADE;")
    op.execute("DROP TABLE derived_layer.topic_quality_metrics CASCADE;")
    op.execute("DROP TABLE derived_layer.topics CASCADE;")
    op.execute("DROP TABLE derived_layer.topic_model_artifacts CASCADE;")
    op.execute("DROP TABLE derived_layer.topic_runs CASCADE;")
    op.execute("DROP TABLE derived_layer.topic_model_profiles CASCADE;")

    op.execute(
        """
        CREATE TABLE derived_layer.topic_runs (
            run_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            workspace_id BIGINT,
            source_field TEXT NOT NULL,
            run_mode TEXT NOT NULL DEFAULT 'full',
            previous_run_id BIGINT,
            status TEXT NOT NULL DEFAULT 'pending',
            input_doc_count INTEGER NOT NULL DEFAULT 0,
            new_doc_count INTEGER NOT NULL DEFAULT 0,
            topic_count INTEGER NOT NULL DEFAULT 0,
            parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            model_artifact_path TEXT,
            model_artifact_hash TEXT,
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            CONSTRAINT topic_runs_workspace_id_fkey
                FOREIGN KEY (workspace_id)
                REFERENCES app_layer.workspaces(workspace_id)
                ON DELETE RESTRICT,
            CONSTRAINT topic_runs_previous_run_id_fkey
                FOREIGN KEY (previous_run_id)
                REFERENCES derived_layer.topic_runs(run_id)
                ON DELETE SET NULL,
            CONSTRAINT topic_runs_source_field_check
                CHECK (source_field = ANY (ARRAY[
                    'wips_independent_claims'::text,
                    'effect_summary'::text
                ])),
            CONSTRAINT topic_runs_run_mode_check
                CHECK (run_mode = ANY (ARRAY['full'::text, 'incremental'::text])),
            CONSTRAINT topic_runs_status_check
                CHECK (status = ANY (ARRAY[
                    'pending'::text,
                    'running'::text,
                    'completed'::text,
                    'failed'::text,
                    'needs_review'::text
                ])),
            CONSTRAINT topic_runs_counts_check
                CHECK (input_doc_count >= 0 AND new_doc_count >= 0 AND topic_count >= 0)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE derived_layer.topics (
            topic_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            run_id BIGINT NOT NULL,
            parent_topic_id BIGINT,
            topic_code TEXT NOT NULL,
            depth INTEGER NOT NULL,
            doc_count INTEGER NOT NULL DEFAULT 0,
            coherence DOUBLE PRECISION,
            diversity DOUBLE PRECISION,
            balance DOUBLE PRECISION,
            keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            representative_patent_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            label TEXT,
            summary TEXT,
            label_source TEXT,
            label_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'accepted',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT topics_run_id_fkey
                FOREIGN KEY (run_id)
                REFERENCES derived_layer.topic_runs(run_id)
                ON DELETE CASCADE,
            CONSTRAINT topics_parent_topic_id_fkey
                FOREIGN KEY (parent_topic_id)
                REFERENCES derived_layer.topics(topic_id)
                ON DELETE CASCADE,
            CONSTRAINT topics_depth_check CHECK (depth >= 1),
            CONSTRAINT topics_doc_count_check CHECK (doc_count >= 0),
            CONSTRAINT topics_label_source_check
                CHECK (label_source IS NULL OR label_source = ANY (ARRAY['llm'::text, 'manual'::text])),
            CONSTRAINT topics_status_check
                CHECK (status = ANY (ARRAY[
                    'accepted'::text,
                    'needs_review'::text,
                    'rejected'::text
                ])),
            CONSTRAINT topics_run_code_key UNIQUE (run_id, topic_code),
            CONSTRAINT topics_id_run_key UNIQUE (topic_id, run_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE derived_layer.topic_assignments (
            run_id BIGINT NOT NULL,
            topic_id BIGINT NOT NULL,
            patent_id BIGINT NOT NULL,
            distance_to_centroid DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, topic_id, patent_id),
            CONSTRAINT topic_assignments_topic_run_fkey
                FOREIGN KEY (topic_id, run_id)
                REFERENCES derived_layer.topics(topic_id, run_id)
                ON DELETE CASCADE,
            CONSTRAINT topic_assignments_patent_id_fkey
                FOREIGN KEY (patent_id)
                REFERENCES core_layer.patents(id)
                ON DELETE CASCADE,
            CONSTRAINT topic_assignments_distance_check
                CHECK (distance_to_centroid IS NULL OR distance_to_centroid >= 0)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE derived_layer.topic_candidates (
            candidate_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            run_id BIGINT NOT NULL,
            parent_topic_id BIGINT,
            candidate_type TEXT NOT NULL,
            candidate_k INTEGER NOT NULL,
            coherence DOUBLE PRECISION,
            diversity DOUBLE PRECISION,
            balance DOUBLE PRECISION,
            score DOUBLE PRECISION,
            parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            llm_explanation TEXT,
            is_selected BOOLEAN NOT NULL DEFAULT false,
            selected_by TEXT,
            selected_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT topic_candidates_run_id_fkey
                FOREIGN KEY (run_id)
                REFERENCES derived_layer.topic_runs(run_id)
                ON DELETE CASCADE,
            CONSTRAINT topic_candidates_parent_topic_run_fkey
                FOREIGN KEY (parent_topic_id, run_id)
                REFERENCES derived_layer.topics(topic_id, run_id)
                ON DELETE CASCADE,
            CONSTRAINT topic_candidates_type_check
                CHECK (candidate_type = ANY (ARRAY[
                    'conservative'::text,
                    'balanced'::text,
                    'detailed'::text
                ])),
            CONSTRAINT topic_candidates_k_check CHECK (candidate_k > 0),
            CONSTRAINT topic_candidates_selection_check
                CHECK (
                    (is_selected AND selected_at IS NOT NULL)
                    OR (NOT is_selected AND selected_at IS NULL AND selected_by IS NULL)
                )
        );
        """
    )

    op.execute("CREATE INDEX idx_topic_runs_workspace_id ON derived_layer.topic_runs(workspace_id);")
    op.execute("CREATE INDEX idx_topic_runs_status ON derived_layer.topic_runs(status);")
    op.execute("CREATE INDEX idx_topic_runs_previous_run_id ON derived_layer.topic_runs(previous_run_id);")
    op.execute("CREATE INDEX idx_topics_run_parent ON derived_layer.topics(run_id, parent_topic_id);")
    op.execute("CREATE INDEX idx_topics_run_depth ON derived_layer.topics(run_id, depth);")
    op.execute("CREATE INDEX idx_topic_assignments_patent_id ON derived_layer.topic_assignments(patent_id);")
    op.execute("CREATE INDEX idx_topic_candidates_run_id ON derived_layer.topic_candidates(run_id);")
    op.execute(
        """
        CREATE UNIQUE INDEX ux_topic_candidates_type
        ON derived_layer.topic_candidates(
            run_id,
            COALESCE(parent_topic_id, 0),
            candidate_type
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_topic_candidates_one_selected
        ON derived_layer.topic_candidates(
            run_id,
            COALESCE(parent_topic_id, 0)
        )
        WHERE is_selected;
        """
    )
