BEGIN;

CREATE SCHEMA IF NOT EXISTS app_layer;

-- Analysis task: one row per analysis run. analysis_id is the shared trace key
-- across outputs and exports. Failure reason is recorded here only.
CREATE TABLE IF NOT EXISTS app_layer.analysis_runs (
    analysis_id BIGSERIAL PRIMARY KEY,
    analysis_name TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    selected_patent_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_analysis_type
    ON app_layer.analysis_runs(analysis_type);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_status
    ON app_layer.analysis_runs(status);

-- Analysis output: one row per produced result (chart data, statistics, AI text).
-- Only successful outputs are written. AI columns are NULL for non-AI outputs.
CREATE TABLE IF NOT EXISTS app_layer.analysis_outputs (
    output_id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL
        REFERENCES app_layer.analysis_runs(analysis_id) ON DELETE RESTRICT,
    output_type TEXT NOT NULL,
    output_name TEXT NOT NULL,
    result_json JSONB NOT NULL,
    ai_model TEXT,
    prompt_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analysis_outputs_analysis_id
    ON app_layer.analysis_outputs(analysis_id);

-- Export run: one row per exported file. The file itself lives on disk;
-- the DB stores path, sha256 hash and parameters for traceability.
CREATE TABLE IF NOT EXISTS app_layer.export_runs (
    export_id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL
        REFERENCES app_layer.analysis_runs(analysis_id) ON DELETE RESTRICT,
    export_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_export_runs_analysis_id
    ON app_layer.export_runs(analysis_id);

COMMIT;
