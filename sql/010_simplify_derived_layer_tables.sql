BEGIN;

DROP TABLE IF EXISTS derived_layer.analysis_result_snapshots;
DROP TABLE IF EXISTS derived_layer.analysis_tasks;
DROP TABLE IF EXISTS derived_layer.report_patent_base;
DROP TABLE IF EXISTS derived_layer.company_aliases;

CREATE TABLE derived_layer.company_aliases (
    id BIGSERIAL PRIMARY KEY,
    "申請人代碼" TEXT,
    "公司名稱" TEXT NOT NULL,
    "別稱" TEXT NOT NULL,
    source_file TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE ("申請人代碼", "公司名稱", "別稱")
);

CREATE INDEX idx_company_aliases_lookup_expr
    ON derived_layer.company_aliases (
        lower(regexp_replace(btrim("別稱"), '\s+', ' ', 'g'))
    );

CREATE INDEX idx_company_aliases_company_name
    ON derived_layer.company_aliases("公司名稱");

CREATE TABLE derived_layer.report_patent_base (
    patent_id BIGINT PRIMARY KEY REFERENCES core_layer.patents(id) ON DELETE CASCADE,
    source_file_id BIGINT REFERENCES raw_layer.source_files(id) ON DELETE SET NULL,
    raw_record_id BIGINT REFERENCES raw_layer.raw_records(id) ON DELETE SET NULL,
    dedupe_key TEXT,
    source_file_name TEXT,
    imported_at TIMESTAMPTZ,

    "授權公告號" TEXT,
    "審查的公告號" TEXT,
    "未審查的公開號" TEXT,
    "申請號" TEXT,
    country_code TEXT,
    database_name TEXT,
    document_kind TEXT,
    patent_type TEXT,
    application_date DATE,
    application_year INTEGER,
    publication_date DATE,
    publication_year INTEGER,
    legal_status TEXT,
    "WIPS同族ID" TEXT,
    title TEXT,
    abstract TEXT,

    "Curr. IPC(Main)" TEXT,
    "Curr. CPC(Main)" TEXT,

    "申請人" TEXT,
    "申請人國籍" TEXT,
    "標準化申請人" TEXT,
    "申請人代表碼" TEXT,
    applicant_display_name TEXT,
    "申請人公司代碼" TEXT,

    "發明人" TEXT,
    "發明人國籍" TEXT,

    "最近專利權人[US,JP,KR,CN,CA,AU]" TEXT,
    "標準當前專利權人[US,JP,KR,CN,CA,AU]" TEXT,
    "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]" TEXT,
    current_assignee_display_name TEXT,
    "專利權人公司代碼" TEXT,

    "最近受讓人[US,KR,CN]" TEXT,
    recent_assignee_display_name TEXT,
    "受讓人公司代碼" TEXT,

    "主權項" TEXT,
    "獨立項[KR,JP,US,CN,EP,IN]" TEXT,
    "所有權利要求[JP,KR,CN]" TEXT,
    "比對用權利要求" TEXT,

    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE TABLE derived_layer.analysis_results (
    id BIGSERIAL PRIMARY KEY,
    analysis_name TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    family_dedup_mode TEXT NOT NULL DEFAULT 'none',
    patent_set_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    comparison_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_analysis_results_analysis_type
    ON derived_layer.analysis_results(analysis_type);

CREATE INDEX idx_analysis_results_status
    ON derived_layer.analysis_results(status);

COMMIT;
