-- Move existing base tables into visible database layers.
-- This does not rewrite table rows. It only changes object schemas and recreates the summary view.

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw_layer;
CREATE SCHEMA IF NOT EXISTS core_layer;
CREATE SCHEMA IF NOT EXISTS derived_layer;
CREATE SCHEMA IF NOT EXISTS app_layer;

COMMENT ON SCHEMA raw_layer IS 'Layer 1 Raw Layer: source file tracking and original raw records.';
COMMENT ON SCHEMA core_layer IS 'Layer 2 Core Layer: normalized patent core tables.';
COMMENT ON SCHEMA derived_layer IS 'Layer 3 Derived / Analytics Layer: reserved for report and analytics tables/views.';
COMMENT ON SCHEMA app_layer IS 'Layer 4 API / Report Layer: reserved for API/report-facing views.';

DROP VIEW IF EXISTS core_layer.patent_source_summary;
DROP VIEW IF EXISTS core_layer.patent_attributes;
DROP VIEW IF EXISTS core_layer.patent_people;
DROP VIEW IF EXISTS core_layer.patent_sources;
DROP VIEW IF EXISTS core_layer.patents;
DROP VIEW IF EXISTS raw_layer.raw_records;
DROP VIEW IF EXISTS raw_layer.source_files;
DROP VIEW IF EXISTS public.patent_source_summary;

ALTER TABLE IF EXISTS public.source_files SET SCHEMA raw_layer;
ALTER TABLE IF EXISTS public.raw_records SET SCHEMA raw_layer;
ALTER TABLE IF EXISTS public.patents SET SCHEMA core_layer;
ALTER TABLE IF EXISTS public.patent_sources SET SCHEMA core_layer;
ALTER TABLE IF EXISTS public.patent_people SET SCHEMA core_layer;
ALTER TABLE IF EXISTS public.patent_attributes SET SCHEMA core_layer;

CREATE OR REPLACE VIEW core_layer.patent_source_summary AS
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
    FROM core_layer.patent_sources
) ps
JOIN raw_layer.source_files sf ON sf.id = ps.source_file_id
GROUP BY ps.patent_id;

ALTER DATABASE patent_ppt SET search_path = core_layer, raw_layer, public;

COMMIT;
