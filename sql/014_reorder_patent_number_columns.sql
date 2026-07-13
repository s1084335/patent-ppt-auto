BEGIN;

CREATE TABLE core_layer.patents_reordered (
    id BIGINT NOT NULL DEFAULT nextval('core_layer.patents_id_seq'::regclass),
    "授權公告號" TEXT,
    "審查的公告號" TEXT,
    "未審查的公開號" TEXT,
    "申請號" TEXT,
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
    "權利要求的項數" TEXT,
    "所有權利要求[JP,KR,CN]" TEXT,
    "主權項" TEXT,
    "主權項(原文)" TEXT,
    "獨立項數量[KR,JP,US,CN,EP,IN]" TEXT,
    "獨立項[KR,JP,US,CN,EP,IN]" TEXT,
    "獨立項(原文)[KR,JP,CN,EP]" TEXT,
    "Orig. CPC(Main)" TEXT,
    "Orig. IPC(Main)" TEXT,
    "Curr. CPC(Main)" TEXT,
    "Curr. IPC(Main)" TEXT,
    legal_status TEXT,
    "WIPS同族ID" TEXT
);

INSERT INTO core_layer.patents_reordered (
    id,
    "授權公告號",
    "審查的公告號",
    "未審查的公開號",
    "申請號",
    country_code,
    database_name,
    document_kind,
    patent_type,
    publication_date,
    publication_year,
    application_date,
    application_year,
    title,
    title_original,
    abstract,
    "權利要求的項數",
    "所有權利要求[JP,KR,CN]",
    "主權項",
    "主權項(原文)",
    "獨立項數量[KR,JP,US,CN,EP,IN]",
    "獨立項[KR,JP,US,CN,EP,IN]",
    "獨立項(原文)[KR,JP,CN,EP]",
    "Orig. CPC(Main)",
    "Orig. IPC(Main)",
    "Curr. CPC(Main)",
    "Curr. IPC(Main)",
    legal_status,
    "WIPS同族ID"
)
SELECT
    id,
    "授權公告號",
    "審查的公告號",
    "未審查的公開號",
    "申請號",
    country_code,
    database_name,
    document_kind,
    patent_type,
    publication_date,
    publication_year,
    application_date,
    application_year,
    title,
    title_original,
    abstract,
    "權利要求的項數",
    "所有權利要求[JP,KR,CN]",
    "主權項",
    "主權項(原文)",
    "獨立項數量[KR,JP,US,CN,EP,IN]",
    "獨立項[KR,JP,US,CN,EP,IN]",
    "獨立項(原文)[KR,JP,CN,EP]",
    "Orig. CPC(Main)",
    "Orig. IPC(Main)",
    "Curr. CPC(Main)",
    "Curr. IPC(Main)",
    legal_status,
    "WIPS同族ID"
FROM core_layer.patents;

CREATE TABLE derived_layer.report_patent_base_reordered (
    patent_id BIGINT NOT NULL,
    dedupe_key TEXT,
    "授權公告號" TEXT,
    "審查的公告號" TEXT,
    "未審查的公開號" TEXT,
    "申請號" TEXT,
    country_code TEXT,
    application_date DATE,
    application_year INTEGER,
    publication_year INTEGER,
    title TEXT,
    "Curr. IPC(Main)" TEXT,
    "Curr. CPC(Main)" TEXT,
    "申請人" TEXT,
    "申請人國籍" TEXT,
    "標準化申請人" TEXT,
    applicant_display_name TEXT,
    "發明人" TEXT,
    "發明人國籍" TEXT,
    "最近專利權人[US,JP,KR,CN,CA,AU]" TEXT,
    "標準當前專利權人[US,JP,KR,CN,CA,AU]" TEXT,
    current_assignee_display_name TEXT,
    "最近受讓人[US,KR,CN]" TEXT,
    recent_assignee_display_name TEXT,
    "主權項" TEXT,
    "獨立項[KR,JP,US,CN,EP,IN]" TEXT,
    "所有權利要求[JP,KR,CN]" TEXT,
    "比對用權利要求" TEXT
);

INSERT INTO derived_layer.report_patent_base_reordered
SELECT
    patent_id,
    dedupe_key,
    "授權公告號",
    "審查的公告號",
    "未審查的公開號",
    "申請號",
    country_code,
    application_date,
    application_year,
    publication_year,
    title,
    "Curr. IPC(Main)",
    "Curr. CPC(Main)",
    "申請人",
    "申請人國籍",
    "標準化申請人",
    applicant_display_name,
    "發明人",
    "發明人國籍",
    "最近專利權人[US,JP,KR,CN,CA,AU]",
    "標準當前專利權人[US,JP,KR,CN,CA,AU]",
    current_assignee_display_name,
    "最近受讓人[US,KR,CN]",
    recent_assignee_display_name,
    "主權項",
    "獨立項[KR,JP,US,CN,EP,IN]",
    "所有權利要求[JP,KR,CN]",
    "比對用權利要求"
FROM derived_layer.report_patent_base;

DROP TABLE derived_layer.report_patent_base;

ALTER TABLE core_layer.patent_sources DROP CONSTRAINT IF EXISTS patent_sources_patent_id_fkey;
ALTER TABLE core_layer.patent_people DROP CONSTRAINT IF EXISTS patent_people_patent_id_fkey;
ALTER TABLE core_layer.patent_attributes DROP CONSTRAINT IF EXISTS patent_attributes_patent_id_fkey;

ALTER SEQUENCE core_layer.patents_id_seq OWNED BY NONE;
DROP TABLE core_layer.patents;
ALTER TABLE core_layer.patents_reordered RENAME TO patents;
ALTER SEQUENCE core_layer.patents_id_seq OWNED BY core_layer.patents.id;

ALTER TABLE core_layer.patents
    ADD CONSTRAINT patents_pkey PRIMARY KEY (id);

SELECT setval(
    'core_layer.patents_id_seq',
    COALESCE((SELECT MAX(id) FROM core_layer.patents), 1),
    (SELECT COUNT(*) > 0 FROM core_layer.patents)
);

CREATE INDEX idx_patents_official_publication_number ON core_layer.patents("授權公告號");
CREATE INDEX idx_patents_examined_publication_number ON core_layer.patents("審查的公告號");
CREATE INDEX idx_patents_publication_number ON core_layer.patents("未審查的公開號");
CREATE INDEX idx_patents_application_number ON core_layer.patents("申請號");
CREATE INDEX idx_patents_publication_year ON core_layer.patents(publication_year);
CREATE INDEX idx_patents_application_year ON core_layer.patents(application_year);
CREATE INDEX idx_patents_country_code ON core_layer.patents(country_code);

ALTER TABLE core_layer.patent_sources
    ADD CONSTRAINT patent_sources_patent_id_fkey
    FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

ALTER TABLE core_layer.patent_people
    ADD CONSTRAINT patent_people_patent_id_fkey
    FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

ALTER TABLE core_layer.patent_attributes
    ADD CONSTRAINT patent_attributes_patent_id_fkey
    FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

ALTER TABLE derived_layer.report_patent_base_reordered RENAME TO report_patent_base;

ALTER TABLE derived_layer.report_patent_base
    ADD CONSTRAINT report_patent_base_pkey PRIMARY KEY (patent_id);

ALTER TABLE derived_layer.report_patent_base
    ADD CONSTRAINT report_patent_base_patent_id_fkey
    FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

CREATE INDEX idx_report_patent_base_application_year
    ON derived_layer.report_patent_base(application_year);

CREATE INDEX idx_report_patent_base_country_code
    ON derived_layer.report_patent_base(country_code);

CREATE INDEX idx_report_patent_base_ipc_main
    ON derived_layer.report_patent_base("Curr. IPC(Main)");

CREATE INDEX idx_report_patent_base_cpc_main
    ON derived_layer.report_patent_base("Curr. CPC(Main)");

CREATE INDEX idx_report_patent_base_applicant_display
    ON derived_layer.report_patent_base(applicant_display_name);

CREATE INDEX idx_report_patent_base_owner_display
    ON derived_layer.report_patent_base(current_assignee_display_name);

COMMIT;
