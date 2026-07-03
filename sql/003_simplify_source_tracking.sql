-- Simplify import tracking and clear old imported data.
-- Run this before re-importing source files.

BEGIN;

TRUNCATE TABLE
    patent_attributes,
    patent_classifications,
    patent_people,
    patent_sources,
    patents,
    raw_records,
    source_files
RESTART IDENTITY CASCADE;

ALTER TABLE source_files DROP CONSTRAINT IF EXISTS source_files_file_hash_key;

ALTER TABLE source_files DROP COLUMN IF EXISTS file_format;
ALTER TABLE source_files DROP COLUMN IF EXISTS sheet_names;
ALTER TABLE source_files DROP COLUMN IF EXISTS selected_sheet;
ALTER TABLE source_files DROP COLUMN IF EXISTS query_text;
ALTER TABLE source_files DROP COLUMN IF EXISTS query_date;
ALTER TABLE source_files DROP COLUMN IF EXISTS mapping_version;

ALTER TABLE source_files ALTER COLUMN file_hash SET NOT NULL;
ALTER TABLE source_files ALTER COLUMN imported_at SET DEFAULT now();

ALTER TABLE patents
    ADD COLUMN IF NOT EXISTS source_summary JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_source_files_file_hash ON source_files(file_hash);
CREATE INDEX IF NOT EXISTS idx_source_files_imported_at ON source_files(imported_at);

COMMIT;
