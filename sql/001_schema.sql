-- Patent tool database schema v1.
-- Target database: patent_ppt

BEGIN;

CREATE TABLE IF NOT EXISTS source_files (
    id BIGSERIAL PRIMARY KEY,
    source_system TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    record_count INTEGER,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_records (
    id BIGSERIAL PRIMARY KEY,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    sheet_name TEXT,
    row_number INTEGER NOT NULL,
    raw_data JSONB NOT NULL,
    UNIQUE (source_file_id, sheet_name, row_number)
);

CREATE TABLE IF NOT EXISTS patents (
    id BIGSERIAL PRIMARY KEY,
    publication_number TEXT,
    application_number TEXT,
    country_code TEXT,
    database_name TEXT,
    document_kind TEXT,
    patent_type TEXT,
    publication_date DATE,
    publication_year INTEGER,
    application_date DATE,
    application_year INTEGER,
    title TEXT,
    title_original TEXT,
    abstract TEXT,
    legal_status TEXT,
    family_id TEXT
);

CREATE TABLE IF NOT EXISTS patent_sources (
    id BIGSERIAL PRIMARY KEY,
    patent_id BIGINT NOT NULL REFERENCES patents(id) ON DELETE CASCADE,
    raw_record_id BIGINT NOT NULL REFERENCES raw_records(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL,
    UNIQUE (patent_id, raw_record_id)
);

CREATE TABLE IF NOT EXISTS patent_people (
    id BIGSERIAL PRIMARY KEY,
    patent_id BIGINT NOT NULL REFERENCES patents(id) ON DELETE CASCADE,
    person_role TEXT NOT NULL,
    name TEXT,
    name_original TEXT,
    country_code TEXT,
    standardized_name TEXT,
    person_code TEXT,
    source_field TEXT
);

CREATE TABLE IF NOT EXISTS patent_attributes (
    id BIGSERIAL PRIMARY KEY,
    patent_id BIGINT NOT NULL REFERENCES patents(id) ON DELETE CASCADE,
    source_group TEXT,
    source_field TEXT NOT NULL,
    attribute_key TEXT NOT NULL,
    attribute_label TEXT NOT NULL,
    attribute_value TEXT,
    value_type TEXT NOT NULL DEFAULT 'text',
    source_file_id BIGINT REFERENCES source_files(id) ON DELETE SET NULL,
    raw_record_id BIGINT REFERENCES raw_records(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_records_source_file_id ON raw_records(source_file_id);
CREATE INDEX IF NOT EXISTS idx_raw_records_raw_data_gin ON raw_records USING GIN (raw_data);

CREATE INDEX IF NOT EXISTS idx_source_files_file_hash ON source_files(file_hash);
CREATE INDEX IF NOT EXISTS idx_source_files_imported_at ON source_files(imported_at);

CREATE INDEX IF NOT EXISTS idx_patents_publication_number ON patents(publication_number);
CREATE INDEX IF NOT EXISTS idx_patents_application_number ON patents(application_number);
CREATE INDEX IF NOT EXISTS idx_patents_publication_year ON patents(publication_year);
CREATE INDEX IF NOT EXISTS idx_patents_application_year ON patents(application_year);
CREATE INDEX IF NOT EXISTS idx_patents_country_code ON patents(country_code);

CREATE INDEX IF NOT EXISTS idx_patent_sources_patent_id ON patent_sources(patent_id);
CREATE INDEX IF NOT EXISTS idx_patent_sources_raw_record_id ON patent_sources(raw_record_id);
CREATE INDEX IF NOT EXISTS idx_patent_sources_dedupe_key ON patent_sources(dedupe_key);

CREATE INDEX IF NOT EXISTS idx_patent_people_patent_id ON patent_people(patent_id);
CREATE INDEX IF NOT EXISTS idx_patent_people_role ON patent_people(person_role);
CREATE INDEX IF NOT EXISTS idx_patent_people_name ON patent_people(name);

CREATE INDEX IF NOT EXISTS idx_patent_attributes_patent_id ON patent_attributes(patent_id);
CREATE INDEX IF NOT EXISTS idx_patent_attributes_source_field ON patent_attributes(source_field);
CREATE INDEX IF NOT EXISTS idx_patent_attributes_key ON patent_attributes(attribute_key);
CREATE INDEX IF NOT EXISTS idx_patent_attributes_key_value_hash
    ON patent_attributes(attribute_key, md5(attribute_value))
    WHERE attribute_value IS NOT NULL;

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
