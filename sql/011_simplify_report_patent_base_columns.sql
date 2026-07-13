BEGIN;

DROP TABLE IF EXISTS derived_layer.report_patent_base;

CREATE TABLE derived_layer.report_patent_base (
    patent_id BIGINT PRIMARY KEY REFERENCES core_layer.patents(id) ON DELETE CASCADE,
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
