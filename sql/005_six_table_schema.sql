-- Reset to 6 base tables + 1 source summary view.
-- This clears imported data and reapplies the simplified schema.

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
DROP VIEW IF EXISTS public.patent_source_summary;

DROP TABLE IF EXISTS
    core_layer.patent_attributes,
    core_layer.patent_classifications,
    core_layer.patent_people,
    core_layer.patent_sources,
    core_layer.patent_registry,
    core_layer.patents,
    raw_layer.raw_records,
    raw_layer.source_files,
    public.patent_attributes,
    public.patent_classifications,
    public.patent_people,
    public.patent_sources,
    public.patent_registry,
    public.patents,
    public.raw_records,
    public.source_files
CASCADE;

CREATE TABLE raw_layer.source_files (
    id BIGSERIAL PRIMARY KEY,
    source_system TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    record_count INTEGER,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE raw_layer.raw_records (
    id BIGSERIAL PRIMARY KEY,
    source_file_id BIGINT NOT NULL REFERENCES raw_layer.source_files(id) ON DELETE CASCADE,
    sheet_name TEXT,
    row_number INTEGER NOT NULL,
    raw_data JSONB NOT NULL,
    UNIQUE (source_file_id, sheet_name, row_number)
);

CREATE TABLE core_layer.patents (
    id BIGSERIAL PRIMARY KEY,
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

CREATE TABLE core_layer.patent_sources (
    id BIGSERIAL PRIMARY KEY,
    patent_id BIGINT NOT NULL REFERENCES core_layer.patents(id) ON DELETE CASCADE,
    raw_record_id BIGINT NOT NULL REFERENCES raw_layer.raw_records(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES raw_layer.source_files(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL,
    UNIQUE (patent_id, raw_record_id)
);

CREATE TABLE core_layer.patent_people (
    id BIGSERIAL PRIMARY KEY,
    patent_id BIGINT NOT NULL REFERENCES core_layer.patents(id) ON DELETE CASCADE,
    "申請人" TEXT,
    "申請人(第2語言)" TEXT,
    "申請人國籍" TEXT,
    "標準化申請人" TEXT,
    "申請人代表碼" TEXT,
    "發明人" TEXT,
    "發明人(第2語言)" TEXT,
    "發明人國籍" TEXT,
    "代理人(機構)" TEXT,
    "最近專利權人[US,JP,KR,CN,CA,AU]" TEXT,
    "最近專利權人(第2語言)[JP,KR,CN]" TEXT,
    "標準當前專利權人[US,JP,KR,CN,CA,AU]" TEXT,
    "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]" TEXT,
    "最近受讓人[US,KR,CN]" TEXT,
    "最近轉讓人[US,KR,CN]" TEXT,
    "申報（登記）人" TEXT,
    "申報（登記）人國籍" TEXT,
    UNIQUE (patent_id)
);

CREATE TABLE core_layer.patent_attributes (
    id BIGSERIAL PRIMARY KEY,
    patent_id BIGINT NOT NULL REFERENCES core_layer.patents(id) ON DELETE CASCADE,
    source_file_id BIGINT REFERENCES raw_layer.source_files(id) ON DELETE SET NULL,
    raw_record_id BIGINT REFERENCES raw_layer.raw_records(id) ON DELETE SET NULL,
    "分類標籤" TEXT,
    "主附圖" TEXT,
    "WIPSGLOBAL KEY" TEXT,
    "摘要(原文)" TEXT,
    "解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]" TEXT,
    "AI摘要[US,EP,PCT,JP,KR,CN,TW]" TEXT,
    "特徵 摘要[US,EP,PCT,JP,KR,CN,TW]" TEXT,
    "解決手段 摘要[US,EP,PCT,JP,KR,CN,TW]" TEXT,
    "效果 摘要[US,EP,PCT,JP,KR,CN,TW]" TEXT,
    "技術領域 摘要[US,EP,PCT,JP,KR,CN,TW]" TEXT,
    "翻譯文提交日" TEXT,
    "發行日[JP,EP,PCT]" TEXT,
    "授權公告日" TEXT,
    "未審查的公開日" TEXT,
    "審查的公告日" TEXT,
    "母案申請日 [KR,JP,EP,CN,IN,CA]" TEXT,
    "優先權申請號" TEXT,
    "關聯申請號 [US,PCT,AU]" TEXT,
    "優先權申請國家" TEXT,
    "優先權國家" TEXT,
    "分案申請 [KR,US,JP,EP,CN,IN,CA,AU]" TEXT,
    "優先權日" TEXT,
    "優先權申請日" TEXT,
    "母案申請號 [KR,JP,EP,CN,IN,CA]" TEXT,
    "關聯申請日 [US,PCT,AU]" TEXT,
    "優先權號" TEXT,
    "PCT申請號" TEXT,
    "PCT公開號" TEXT,
    "指定國家代碼" TEXT,
    "PCT申請日" TEXT,
    "PCT公開日" TEXT,
    "Orig. FI[JP]" TEXT,
    "Orig. F-term[JP]" TEXT,
    "Curr. IPC(All)" TEXT,
    "Orig. IPC(All)" TEXT,
    "Curr. FI[JP]" TEXT,
    "Orig. US Class(All)[US]" TEXT,
    "Curr. CPC(All)" TEXT,
    "Orig. US Class(Main)[US]" TEXT,
    "Curr. US Class(Main)[US]" TEXT,
    "Curr. F-term[JP]" TEXT,
    "Orig. CPC(All)" TEXT,
    "Curr. US Class(All)[US]" TEXT,
    "Orig. Theme Code[JP]" TEXT,
    "(B1)引用文獻號碼" TEXT,
    "(F1)他引被引文獻號碼" TEXT,
    "(F1)引用文獻數" TEXT,
    "(B1)他引文獻號碼" TEXT,
    "(F1)引用文獻號碼" TEXT,
    "(F1)審查官引用文獻[US,JP,KR,EP]" TEXT,
    "(B1)非專利參考文獻數" TEXT,
    "(B1)非專利參考文獻" TEXT,
    "(B1)自引文獻號碼" TEXT,
    "(B1)審查官引用文獻[US,JP,KR,EP]" TEXT,
    "(B1)引用文獻數" TEXT,
    "(F1)自引被引文獻號碼" TEXT,
    "EPO同族專利個别國家文獻數量(申請為準)" TEXT,
    "WIPS同族專利Basic patent編號" TEXT,
    "WIPS同族各國家文獻數量(申請為準)" TEXT,
    "EPO同族ID" TEXT,
    "WIPS同族國家數量(申請為準)" TEXT,
    "EPO同族國家數量(申請為準)" TEXT,
    "WIPS同族文獻編號(申請基準)" TEXT,
    "EPO同族文獻數量(申請為準)" TEXT,
    "WIPS同族文獻數量(申請為準)" TEXT,
    "EPO同族專利文獻號碼(申請為準)" TEXT,
    "統一專利法院[EP]" TEXT,
    "DOCDB法律狀態" TEXT,
    "實體狀態[US]" TEXT,
    "(預計)到期日期[US,JP,KR,CN,EP,CA,AU]" TEXT,
    "最近年費繳納日[US,EP,KR]" TEXT,
    "EPC指定國[EP]" TEXT,
    "EPC有效國家[EP]" TEXT,
    "EPC無效國家[EP]" TEXT,
    "PTA延長日期[US]" TEXT,
    "AIA適用[US]" TEXT,
    "韓國標準當前專利權人" TEXT,
    "最近轉讓日[US,KR,CN]" TEXT,
    "被許可人數量[KR]" TEXT,
    "權利變動[US,KR,CN]" TEXT,
    "實施許可[KR]" TEXT,
    "最近轉讓類型[US,KR,CN]" TEXT,
    "是否請求審查(日期)[JP,KR,EP,CA]" TEXT,
    "審判總數[US,JP,KR,EP]" TEXT,
    "優先請求審查[KR]" TEXT,
    "意見提出通知書次數[KR]" TEXT,
    "拒絕決定[JP,KR]" TEXT,
    "再審申請[KR]" TEXT,
    "新穎性喪失例外主張[JP]" TEXT,
    "訴訟總數[US]" TEXT,
    "管轄法院類型[US]" TEXT,
    "審判管轄類型[US,JP,KR,EP]" TEXT,
    "審查員[US,JP,KR,CN]" TEXT,
    "個別圖數量" TEXT,
    "文圖像文件(PDF)連結" TEXT,
    "食品藥品專利記載[US]" TEXT,
    "詳細查看連結(登入)" TEXT,
    "文獻備註" TEXT,
    "糾正公告存在[JP,KR]" TEXT,
    "標準化機構" TEXT,
    "申報日" TEXT,
    "標準號碼" TEXT,
    "申請人名称標準化代碼[JP]" TEXT,
    "發明人數" TEXT,
    "標準化申請人[KR]" TEXT,
    "申請人數" TEXT
);

CREATE INDEX idx_raw_records_source_file_id ON raw_layer.raw_records(source_file_id);
CREATE INDEX idx_raw_records_raw_data_gin ON raw_layer.raw_records USING GIN (raw_data);

CREATE INDEX idx_source_files_file_hash ON raw_layer.source_files(file_hash);
CREATE INDEX idx_source_files_imported_at ON raw_layer.source_files(imported_at);

CREATE INDEX idx_patents_official_publication_number ON core_layer.patents("授權公告號");
CREATE INDEX idx_patents_examined_publication_number ON core_layer.patents("審查的公告號");
CREATE INDEX idx_patents_publication_number ON core_layer.patents("未審查的公開號");
CREATE INDEX idx_patents_application_number ON core_layer.patents("申請號");
CREATE INDEX idx_patents_publication_year ON core_layer.patents(publication_year);
CREATE INDEX idx_patents_application_year ON core_layer.patents(application_year);
CREATE INDEX idx_patents_country_code ON core_layer.patents(country_code);

CREATE INDEX idx_patent_sources_patent_id ON core_layer.patent_sources(patent_id);
CREATE INDEX idx_patent_sources_raw_record_id ON core_layer.patent_sources(raw_record_id);
CREATE INDEX idx_patent_sources_dedupe_key ON core_layer.patent_sources(dedupe_key);

CREATE INDEX idx_patent_people_patent_id ON core_layer.patent_people(patent_id);
CREATE INDEX idx_patent_people_applicant ON core_layer.patent_people("申請人");
CREATE INDEX idx_patent_people_owner ON core_layer.patent_people("最近專利權人[US,JP,KR,CN,CA,AU]");
CREATE INDEX idx_patent_people_assignee ON core_layer.patent_people("最近受讓人[US,KR,CN]");
CREATE INDEX idx_patent_people_inventor ON core_layer.patent_people("發明人");

CREATE INDEX idx_patent_attributes_patent_id ON core_layer.patent_attributes(patent_id);
CREATE VIEW core_layer.patent_source_summary AS
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
