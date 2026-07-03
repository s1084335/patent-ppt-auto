-- Keep patent content fields separate from system/import fields.
-- Run this before re-importing source files.

BEGIN;

DROP VIEW IF EXISTS patent_source_summary;

TRUNCATE TABLE
    patent_attributes,
    patent_classifications,
    patent_people,
    patent_sources,
    patents,
    raw_records,
    source_files
RESTART IDENTITY CASCADE;

CREATE TABLE IF NOT EXISTS patent_registry (
    patent_id BIGINT PRIMARY KEY REFERENCES patents(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE patents DROP COLUMN IF EXISTS dedupe_key;
ALTER TABLE patents DROP COLUMN IF EXISTS source_summary;
ALTER TABLE patents DROP COLUMN IF EXISTS created_at;
ALTER TABLE patents DROP COLUMN IF EXISTS updated_at;

ALTER TABLE raw_records DROP COLUMN IF EXISTS created_at;
ALTER TABLE raw_records DROP COLUMN IF EXISTS source_system;
ALTER TABLE patent_sources DROP COLUMN IF EXISTS created_at;
ALTER TABLE patent_sources DROP COLUMN IF EXISTS source_system;
ALTER TABLE patent_people DROP COLUMN IF EXISTS created_at;
ALTER TABLE patent_people DROP COLUMN IF EXISTS sequence;
ALTER TABLE patent_classifications DROP COLUMN IF EXISTS created_at;
ALTER TABLE patent_classifications DROP COLUMN IF EXISTS is_primary;
ALTER TABLE patent_classifications DROP COLUMN IF EXISTS is_original;
ALTER TABLE patent_classifications DROP COLUMN IF EXISTS is_current;
ALTER TABLE patent_classifications DROP COLUMN IF EXISTS sequence;
ALTER TABLE patent_attributes DROP COLUMN IF EXISTS created_at;
ALTER TABLE patent_attributes DROP COLUMN IF EXISTS source_system;

CREATE INDEX IF NOT EXISTS idx_patent_registry_dedupe_key ON patent_registry(dedupe_key);

CREATE OR REPLACE VIEW patent_source_summary AS
SELECT
    ps.patent_id,
    jsonb_agg(
        jsonb_build_object(
            'source_system', sf.source_system,
            'file_name', sf.file_name,
            'file_hash', sf.file_hash,
            'imported_at', sf.imported_at
        )
        ORDER BY sf.imported_at, sf.id
    ) AS source_summary
FROM (
    SELECT DISTINCT patent_id, source_file_id
    FROM patent_sources
) ps
JOIN source_files sf ON sf.id = ps.source_file_id
GROUP BY ps.patent_id;

COMMIT;
