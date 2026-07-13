--
-- PostgreSQL database dump
--

-- Dumped from database version 18.4
-- Dumped by pg_dump version 18.4

--
-- Name: app_layer; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA app_layer;

--
-- Name: SCHEMA app_layer; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA app_layer IS 'Layer 4 API / Report Layer: reserved for API/report-facing views.';

--
-- Name: core_layer; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA core_layer;

--
-- Name: SCHEMA core_layer; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA core_layer IS 'Layer 2 Core Layer: normalized patent core tables.';

--
-- Name: derived_layer; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA derived_layer;

--
-- Name: SCHEMA derived_layer; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA derived_layer IS 'Layer 3 Derived / Analytics Layer: reserved for report and analytics tables/views.';

--
-- Name: raw_layer; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA raw_layer;

--
-- Name: SCHEMA raw_layer; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA raw_layer IS 'Layer 1 Raw Layer: source file tracking and original raw records.';

--
-- Name: analysis_outputs; Type: TABLE; Schema: app_layer; Owner: -
--

CREATE TABLE app_layer.analysis_outputs (
    output_id bigint NOT NULL,
    analysis_id bigint NOT NULL,
    output_type text NOT NULL,
    output_name text NOT NULL,
    result_json jsonb NOT NULL,
    ai_model text,
    prompt_version text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: analysis_outputs_output_id_seq; Type: SEQUENCE; Schema: app_layer; Owner: -
--

CREATE SEQUENCE app_layer.analysis_outputs_output_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: analysis_outputs_output_id_seq; Type: SEQUENCE OWNED BY; Schema: app_layer; Owner: -
--

ALTER SEQUENCE app_layer.analysis_outputs_output_id_seq OWNED BY app_layer.analysis_outputs.output_id;

--
-- Name: analysis_runs; Type: TABLE; Schema: app_layer; Owner: -
--

CREATE TABLE app_layer.analysis_runs (
    analysis_id bigint NOT NULL,
    analysis_name text NOT NULL,
    analysis_type text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    filter_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    parameters_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    selected_patent_ids_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT analysis_runs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'completed'::text, 'failed'::text])))
);

--
-- Name: analysis_runs_analysis_id_seq; Type: SEQUENCE; Schema: app_layer; Owner: -
--

CREATE SEQUENCE app_layer.analysis_runs_analysis_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: analysis_runs_analysis_id_seq; Type: SEQUENCE OWNED BY; Schema: app_layer; Owner: -
--

ALTER SEQUENCE app_layer.analysis_runs_analysis_id_seq OWNED BY app_layer.analysis_runs.analysis_id;

--
-- Name: export_runs; Type: TABLE; Schema: app_layer; Owner: -
--

CREATE TABLE app_layer.export_runs (
    export_id bigint NOT NULL,
    analysis_id bigint NOT NULL,
    export_type text NOT NULL,
    file_path text NOT NULL,
    file_hash text NOT NULL,
    parameters_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: export_runs_export_id_seq; Type: SEQUENCE; Schema: app_layer; Owner: -
--

CREATE SEQUENCE app_layer.export_runs_export_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: export_runs_export_id_seq; Type: SEQUENCE OWNED BY; Schema: app_layer; Owner: -
--

ALTER SEQUENCE app_layer.export_runs_export_id_seq OWNED BY app_layer.export_runs.export_id;

--
-- Name: patent_attributes; Type: TABLE; Schema: core_layer; Owner: -
--

CREATE TABLE core_layer.patent_attributes (
    id bigint NOT NULL,
    patent_id bigint NOT NULL,
    source_file_id bigint,
    raw_record_id bigint,
    "分類標籤" text,
    "主附圖" text,
    "WIPSGLOBAL KEY" text,
    "摘要(原文)" text,
    "解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]" text,
    "AI摘要[US,EP,PCT,JP,KR,CN,TW]" text,
    "特徵 摘要[US,EP,PCT,JP,KR,CN,TW]" text,
    "解決手段 摘要[US,EP,PCT,JP,KR,CN,TW]" text,
    "效果 摘要[US,EP,PCT,JP,KR,CN,TW]" text,
    "技術領域 摘要[US,EP,PCT,JP,KR,CN,TW]" text,
    "翻譯文提交日" text,
    "發行日[JP,EP,PCT]" text,
    "授權公告日" text,
    "未審查的公開日" text,
    "審查的公告號" text,
    "審查的公告日" text,
    "母案申請日 [KR,JP,EP,CN,IN,CA]" text,
    "優先權申請號" text,
    "關聯申請號 [US,PCT,AU]" text,
    "優先權申請國家" text,
    "優先權國家" text,
    "分案申請 [KR,US,JP,EP,CN,IN,CA,AU]" text,
    "優先權日" text,
    "優先權申請日" text,
    "母案申請號 [KR,JP,EP,CN,IN,CA]" text,
    "關聯申請日 [US,PCT,AU]" text,
    "優先權號" text,
    "PCT申請號" text,
    "PCT公開號" text,
    "指定國家代碼" text,
    "PCT申請日" text,
    "PCT公開日" text,
    "Orig. FI[JP]" text,
    "Orig. F-term[JP]" text,
    "Curr. IPC(All)" text,
    "Orig. IPC(All)" text,
    "Curr. FI[JP]" text,
    "Orig. US Class(All)[US]" text,
    "Curr. CPC(All)" text,
    "Orig. US Class(Main)[US]" text,
    "Curr. US Class(Main)[US]" text,
    "Curr. F-term[JP]" text,
    "Orig. CPC(All)" text,
    "Curr. US Class(All)[US]" text,
    "Orig. Theme Code[JP]" text,
    "(B1)引用文獻號碼" text,
    "(F1)他引被引文獻號碼" text,
    "(F1)引用文獻數" text,
    "(B1)他引文獻號碼" text,
    "(F1)引用文獻號碼" text,
    "(F1)審查官引用文獻[US,JP,KR,EP]" text,
    "(B1)非專利參考文獻數" text,
    "(B1)非專利參考文獻" text,
    "(B1)自引文獻號碼" text,
    "(B1)審查官引用文獻[US,JP,KR,EP]" text,
    "(B1)引用文獻數" text,
    "(F1)自引被引文獻號碼" text,
    "EPO同族專利個别國家文獻數量(申請為準)" text,
    "WIPS同族專利Basic patent編號" text,
    "WIPS同族各國家文獻數量(申請為準)" text,
    "EPO同族ID" text,
    "WIPS同族國家數量(申請為準)" text,
    "EPO同族國家數量(申請為準)" text,
    "WIPS同族文獻編號(申請基準)" text,
    "EPO同族文獻數量(申請為準)" text,
    "WIPS同族文獻數量(申請為準)" text,
    "EPO同族專利文獻號碼(申請為準)" text,
    "統一專利法院[EP]" text,
    "DOCDB法律狀態" text,
    "實體狀態[US]" text,
    "(預計)到期日期[US,JP,KR,CN,EP,CA,AU]" text,
    "最近年費繳納日[US,EP,KR]" text,
    "EPC指定國[EP]" text,
    "EPC有效國家[EP]" text,
    "EPC無效國家[EP]" text,
    "PTA延長日期[US]" text,
    "AIA適用[US]" text,
    "韓國標準當前專利權人" text,
    "最近轉讓日[US,KR,CN]" text,
    "被許可人數量[KR]" text,
    "權利變動[US,KR,CN]" text,
    "實施許可[KR]" text,
    "最近轉讓類型[US,KR,CN]" text,
    "是否請求審查(日期)[JP,KR,EP,CA]" text,
    "審判總數[US,JP,KR,EP]" text,
    "優先請求審查[KR]" text,
    "意見提出通知書次數[KR]" text,
    "拒絕決定[JP,KR]" text,
    "再審申請[KR]" text,
    "新穎性喪失例外主張[JP]" text,
    "訴訟總數[US]" text,
    "管轄法院類型[US]" text,
    "審判管轄類型[US,JP,KR,EP]" text,
    "審查員[US,JP,KR,CN]" text,
    "個別圖數量" text,
    "文圖像文件(PDF)連結" text,
    "食品藥品專利記載[US]" text,
    "詳細查看連結(登入)" text,
    "文獻備註" text,
    "糾正公告存在[JP,KR]" text,
    "標準化機構" text,
    "申報日" text,
    "標準號碼" text,
    "申請人名称標準化代碼[JP]" text,
    "發明人數" text,
    "標準化申請人[KR]" text,
    "申請人數" text
);

--
-- Name: patent_attributes_id_seq; Type: SEQUENCE; Schema: core_layer; Owner: -
--

CREATE SEQUENCE core_layer.patent_attributes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: patent_attributes_id_seq; Type: SEQUENCE OWNED BY; Schema: core_layer; Owner: -
--

ALTER SEQUENCE core_layer.patent_attributes_id_seq OWNED BY core_layer.patent_attributes.id;

--
-- Name: patent_people; Type: TABLE; Schema: core_layer; Owner: -
--

CREATE TABLE core_layer.patent_people (
    id bigint NOT NULL,
    patent_id bigint NOT NULL,
    "申請人" text,
    "申請人(第2語言)" text,
    "申請人國籍" text,
    "標準化申請人" text,
    "申請人代表碼" text,
    "發明人" text,
    "發明人(第2語言)" text,
    "發明人國籍" text,
    "代理人(機構)" text,
    "最近專利權人[US,JP,KR,CN,CA,AU]" text,
    "最近專利權人(第2語言)[JP,KR,CN]" text,
    "標準當前專利權人[US,JP,KR,CN,CA,AU]" text,
    "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]" text,
    "最近受讓人[US,KR,CN]" text,
    "最近轉讓人[US,KR,CN]" text,
    "申報（登記）人" text,
    "申報（登記）人國籍" text
);

--
-- Name: patent_people_id_seq; Type: SEQUENCE; Schema: core_layer; Owner: -
--

CREATE SEQUENCE core_layer.patent_people_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: patent_people_id_seq; Type: SEQUENCE OWNED BY; Schema: core_layer; Owner: -
--

ALTER SEQUENCE core_layer.patent_people_id_seq OWNED BY core_layer.patent_people.id;

--
-- Name: patent_sources; Type: TABLE; Schema: core_layer; Owner: -
--

CREATE TABLE core_layer.patent_sources (
    id bigint NOT NULL,
    patent_id bigint NOT NULL,
    raw_record_id bigint NOT NULL,
    source_file_id bigint NOT NULL,
    dedupe_key text NOT NULL
);

--
-- Name: source_files; Type: TABLE; Schema: raw_layer; Owner: -
--

CREATE TABLE raw_layer.source_files (
    id bigint NOT NULL,
    source_system text NOT NULL,
    file_name text NOT NULL,
    file_path text NOT NULL,
    file_hash text NOT NULL,
    record_count integer,
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: patent_source_summary; Type: VIEW; Schema: core_layer; Owner: -
--

CREATE VIEW core_layer.patent_source_summary AS
 SELECT ps.patent_id,
    jsonb_agg(jsonb_build_object('source_system', sf.source_system, 'file_name', sf.file_name, 'file_hash', sf.file_hash, 'imported_at', sf.imported_at) ORDER BY sf.imported_at, sf.id) AS source_summary
   FROM (( SELECT DISTINCT patent_sources.patent_id,
            patent_sources.source_file_id
           FROM core_layer.patent_sources) ps
     JOIN raw_layer.source_files sf ON ((sf.id = ps.source_file_id)))
  GROUP BY ps.patent_id;

--
-- Name: patent_sources_id_seq; Type: SEQUENCE; Schema: core_layer; Owner: -
--

CREATE SEQUENCE core_layer.patent_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: patent_sources_id_seq; Type: SEQUENCE OWNED BY; Schema: core_layer; Owner: -
--

ALTER SEQUENCE core_layer.patent_sources_id_seq OWNED BY core_layer.patent_sources.id;

--
-- Name: patents; Type: TABLE; Schema: core_layer; Owner: -
--

CREATE TABLE core_layer.patents (
    id bigint NOT NULL,
    "授權公告號" text,
    "未審查的公開號" text,
    "申請號" text,
    country_code text,
    database_name text,
    document_kind text,
    patent_type text,
    publication_date date,
    publication_year integer,
    application_date date,
    application_year integer,
    title text,
    title_original text,
    abstract text,
    "權利要求的項數" text,
    "所有權利要求[JP,KR,CN]" text,
    "主權項" text,
    "主權項(原文)" text,
    "獨立項數量[KR,JP,US,CN,EP,IN]" text,
    "獨立項[KR,JP,US,CN,EP,IN]" text,
    "獨立項(原文)[KR,JP,CN,EP]" text,
    legal_status text,
    "WIPS同族ID" text,
    "Orig. CPC(Main)" text,
    "Orig. IPC(Main)" text,
    "Curr. CPC(Main)" text,
    "Curr. IPC(Main)" text
);

--
-- Name: patents_id_seq; Type: SEQUENCE; Schema: core_layer; Owner: -
--

CREATE SEQUENCE core_layer.patents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: patents_id_seq; Type: SEQUENCE OWNED BY; Schema: core_layer; Owner: -
--

ALTER SEQUENCE core_layer.patents_id_seq OWNED BY core_layer.patents.id;

--
-- Name: analysis_results; Type: TABLE; Schema: derived_layer; Owner: -
--

CREATE TABLE derived_layer.analysis_results (
    id bigint NOT NULL,
    analysis_name text NOT NULL,
    analysis_type text NOT NULL,
    filter_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    family_dedup_mode text DEFAULT 'none'::text NOT NULL,
    patent_set_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    metrics_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    comparison_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: analysis_results_id_seq; Type: SEQUENCE; Schema: derived_layer; Owner: -
--

CREATE SEQUENCE derived_layer.analysis_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: analysis_results_id_seq; Type: SEQUENCE OWNED BY; Schema: derived_layer; Owner: -
--

ALTER SEQUENCE derived_layer.analysis_results_id_seq OWNED BY derived_layer.analysis_results.id;

--
-- Name: company_aliases; Type: TABLE; Schema: derived_layer; Owner: -
--

CREATE TABLE derived_layer.company_aliases (
    id bigint NOT NULL,
    "申請人代碼" text,
    "公司名稱" text NOT NULL,
    "別稱" text NOT NULL,
    source_file text,
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: company_aliases_id_seq; Type: SEQUENCE; Schema: derived_layer; Owner: -
--

CREATE SEQUENCE derived_layer.company_aliases_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: company_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: derived_layer; Owner: -
--

ALTER SEQUENCE derived_layer.company_aliases_id_seq OWNED BY derived_layer.company_aliases.id;

--
-- Name: report_patent_base; Type: TABLE; Schema: derived_layer; Owner: -
--

CREATE TABLE derived_layer.report_patent_base (
    patent_id bigint NOT NULL,
    dedupe_key text,
    "授權公告號" text,
    "未審查的公開號" text,
    "申請號" text,
    country_code text,
    application_date date,
    application_year integer,
    publication_year integer,
    title text,
    "Curr. IPC(Main)" text,
    "Curr. CPC(Main)" text,
    "申請人" text,
    "申請人國籍" text,
    "標準化申請人" text,
    applicant_display_name text,
    "發明人" text,
    "發明人國籍" text,
    "最近專利權人[US,JP,KR,CN,CA,AU]" text,
    "標準當前專利權人[US,JP,KR,CN,CA,AU]" text,
    current_assignee_display_name text,
    "最近受讓人[US,KR,CN]" text,
    recent_assignee_display_name text,
    "主權項" text,
    "獨立項[KR,JP,US,CN,EP,IN]" text,
    "所有權利要求[JP,KR,CN]" text,
    "比對用權利要求" text
);

--
-- Name: raw_records; Type: TABLE; Schema: raw_layer; Owner: -
--

CREATE TABLE raw_layer.raw_records (
    id bigint NOT NULL,
    source_file_id bigint NOT NULL,
    sheet_name text,
    row_number integer NOT NULL,
    raw_data jsonb NOT NULL
);

--
-- Name: raw_records_id_seq; Type: SEQUENCE; Schema: raw_layer; Owner: -
--

CREATE SEQUENCE raw_layer.raw_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: raw_records_id_seq; Type: SEQUENCE OWNED BY; Schema: raw_layer; Owner: -
--

ALTER SEQUENCE raw_layer.raw_records_id_seq OWNED BY raw_layer.raw_records.id;

--
-- Name: source_files_id_seq; Type: SEQUENCE; Schema: raw_layer; Owner: -
--

CREATE SEQUENCE raw_layer.source_files_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: source_files_id_seq; Type: SEQUENCE OWNED BY; Schema: raw_layer; Owner: -
--

ALTER SEQUENCE raw_layer.source_files_id_seq OWNED BY raw_layer.source_files.id;

--
-- Name: analysis_outputs output_id; Type: DEFAULT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.analysis_outputs ALTER COLUMN output_id SET DEFAULT nextval('app_layer.analysis_outputs_output_id_seq'::regclass);

--
-- Name: analysis_runs analysis_id; Type: DEFAULT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.analysis_runs ALTER COLUMN analysis_id SET DEFAULT nextval('app_layer.analysis_runs_analysis_id_seq'::regclass);

--
-- Name: export_runs export_id; Type: DEFAULT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.export_runs ALTER COLUMN export_id SET DEFAULT nextval('app_layer.export_runs_export_id_seq'::regclass);

--
-- Name: patent_attributes id; Type: DEFAULT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_attributes ALTER COLUMN id SET DEFAULT nextval('core_layer.patent_attributes_id_seq'::regclass);

--
-- Name: patent_people id; Type: DEFAULT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_people ALTER COLUMN id SET DEFAULT nextval('core_layer.patent_people_id_seq'::regclass);

--
-- Name: patent_sources id; Type: DEFAULT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_sources ALTER COLUMN id SET DEFAULT nextval('core_layer.patent_sources_id_seq'::regclass);

--
-- Name: patents id; Type: DEFAULT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patents ALTER COLUMN id SET DEFAULT nextval('core_layer.patents_id_seq'::regclass);

--
-- Name: analysis_results id; Type: DEFAULT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.analysis_results ALTER COLUMN id SET DEFAULT nextval('derived_layer.analysis_results_id_seq'::regclass);

--
-- Name: company_aliases id; Type: DEFAULT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.company_aliases ALTER COLUMN id SET DEFAULT nextval('derived_layer.company_aliases_id_seq'::regclass);

--
-- Name: raw_records id; Type: DEFAULT; Schema: raw_layer; Owner: -
--

ALTER TABLE ONLY raw_layer.raw_records ALTER COLUMN id SET DEFAULT nextval('raw_layer.raw_records_id_seq'::regclass);

--
-- Name: source_files id; Type: DEFAULT; Schema: raw_layer; Owner: -
--

ALTER TABLE ONLY raw_layer.source_files ALTER COLUMN id SET DEFAULT nextval('raw_layer.source_files_id_seq'::regclass);

--
-- Name: analysis_outputs analysis_outputs_pkey; Type: CONSTRAINT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.analysis_outputs
    ADD CONSTRAINT analysis_outputs_pkey PRIMARY KEY (output_id);

--
-- Name: analysis_runs analysis_runs_pkey; Type: CONSTRAINT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.analysis_runs
    ADD CONSTRAINT analysis_runs_pkey PRIMARY KEY (analysis_id);

--
-- Name: export_runs export_runs_pkey; Type: CONSTRAINT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.export_runs
    ADD CONSTRAINT export_runs_pkey PRIMARY KEY (export_id);

--
-- Name: patent_attributes patent_attributes_pkey; Type: CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_attributes
    ADD CONSTRAINT patent_attributes_pkey PRIMARY KEY (id);

--
-- Name: patent_people patent_people_patent_id_key; Type: CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_people
    ADD CONSTRAINT patent_people_patent_id_key UNIQUE (patent_id);

--
-- Name: patent_people patent_people_pkey; Type: CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_people
    ADD CONSTRAINT patent_people_pkey PRIMARY KEY (id);

--
-- Name: patent_sources patent_sources_patent_id_raw_record_id_key; Type: CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_sources
    ADD CONSTRAINT patent_sources_patent_id_raw_record_id_key UNIQUE (patent_id, raw_record_id);

--
-- Name: patent_sources patent_sources_pkey; Type: CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_sources
    ADD CONSTRAINT patent_sources_pkey PRIMARY KEY (id);

--
-- Name: patents patents_pkey; Type: CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patents
    ADD CONSTRAINT patents_pkey PRIMARY KEY (id);

--
-- Name: analysis_results analysis_results_pkey; Type: CONSTRAINT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.analysis_results
    ADD CONSTRAINT analysis_results_pkey PRIMARY KEY (id);

--
-- Name: company_aliases company_aliases_pkey; Type: CONSTRAINT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.company_aliases
    ADD CONSTRAINT company_aliases_pkey PRIMARY KEY (id);

--
-- Name: company_aliases company_aliases_申請人代碼_公司名稱_別稱_key; Type: CONSTRAINT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.company_aliases
    ADD CONSTRAINT "company_aliases_申請人代碼_公司名稱_別稱_key" UNIQUE ("申請人代碼", "公司名稱", "別稱");

--
-- Name: report_patent_base report_patent_base_pkey; Type: CONSTRAINT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.report_patent_base
    ADD CONSTRAINT report_patent_base_pkey PRIMARY KEY (patent_id);

--
-- Name: raw_records raw_records_pkey; Type: CONSTRAINT; Schema: raw_layer; Owner: -
--

ALTER TABLE ONLY raw_layer.raw_records
    ADD CONSTRAINT raw_records_pkey PRIMARY KEY (id);

--
-- Name: raw_records raw_records_source_file_id_sheet_name_row_number_key; Type: CONSTRAINT; Schema: raw_layer; Owner: -
--

ALTER TABLE ONLY raw_layer.raw_records
    ADD CONSTRAINT raw_records_source_file_id_sheet_name_row_number_key UNIQUE (source_file_id, sheet_name, row_number);

--
-- Name: source_files source_files_pkey; Type: CONSTRAINT; Schema: raw_layer; Owner: -
--

ALTER TABLE ONLY raw_layer.source_files
    ADD CONSTRAINT source_files_pkey PRIMARY KEY (id);

--
-- Name: idx_analysis_outputs_analysis_id; Type: INDEX; Schema: app_layer; Owner: -
--

CREATE INDEX idx_analysis_outputs_analysis_id ON app_layer.analysis_outputs USING btree (analysis_id);

--
-- Name: idx_analysis_runs_analysis_type; Type: INDEX; Schema: app_layer; Owner: -
--

CREATE INDEX idx_analysis_runs_analysis_type ON app_layer.analysis_runs USING btree (analysis_type);

--
-- Name: idx_analysis_runs_status; Type: INDEX; Schema: app_layer; Owner: -
--

CREATE INDEX idx_analysis_runs_status ON app_layer.analysis_runs USING btree (status);

--
-- Name: idx_export_runs_analysis_id; Type: INDEX; Schema: app_layer; Owner: -
--

CREATE INDEX idx_export_runs_analysis_id ON app_layer.export_runs USING btree (analysis_id);

--
-- Name: idx_patent_attributes_patent_id; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_attributes_patent_id ON core_layer.patent_attributes USING btree (patent_id);

--
-- Name: idx_patent_people_applicant; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_people_applicant ON core_layer.patent_people USING btree ("申請人");

--
-- Name: idx_patent_people_assignee; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_people_assignee ON core_layer.patent_people USING btree ("最近受讓人[US,KR,CN]");

--
-- Name: idx_patent_people_inventor; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_people_inventor ON core_layer.patent_people USING btree ("發明人");

--
-- Name: idx_patent_people_owner; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_people_owner ON core_layer.patent_people USING btree ("最近專利權人[US,JP,KR,CN,CA,AU]");

--
-- Name: idx_patent_people_patent_id; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_people_patent_id ON core_layer.patent_people USING btree (patent_id);

--
-- Name: idx_patent_sources_dedupe_key; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_sources_dedupe_key ON core_layer.patent_sources USING btree (dedupe_key);

--
-- Name: idx_patent_sources_patent_id; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_sources_patent_id ON core_layer.patent_sources USING btree (patent_id);

--
-- Name: idx_patent_sources_raw_record_id; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patent_sources_raw_record_id ON core_layer.patent_sources USING btree (raw_record_id);

--
-- Name: idx_patents_application_number; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patents_application_number ON core_layer.patents USING btree ("申請號");

--
-- Name: idx_patents_application_year; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patents_application_year ON core_layer.patents USING btree (application_year);

--
-- Name: idx_patents_country_code; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patents_country_code ON core_layer.patents USING btree (country_code);

--
-- Name: idx_patents_official_publication_number; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patents_official_publication_number ON core_layer.patents USING btree ("授權公告號");

--
-- Name: idx_patents_publication_number; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patents_publication_number ON core_layer.patents USING btree ("未審查的公開號");

--
-- Name: idx_patents_publication_year; Type: INDEX; Schema: core_layer; Owner: -
--

CREATE INDEX idx_patents_publication_year ON core_layer.patents USING btree (publication_year);

--
-- Name: idx_analysis_results_analysis_type; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_analysis_results_analysis_type ON derived_layer.analysis_results USING btree (analysis_type);

--
-- Name: idx_analysis_results_status; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_analysis_results_status ON derived_layer.analysis_results USING btree (status);

--
-- Name: idx_company_aliases_company_name; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_company_aliases_company_name ON derived_layer.company_aliases USING btree ("公司名稱");

--
-- Name: idx_company_aliases_lookup_expr; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_company_aliases_lookup_expr ON derived_layer.company_aliases USING btree (lower(regexp_replace(btrim("別稱"), '\s+'::text, ' '::text, 'g'::text)));

--
-- Name: idx_report_patent_base_applicant_display; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_report_patent_base_applicant_display ON derived_layer.report_patent_base USING btree (applicant_display_name);

--
-- Name: idx_report_patent_base_application_year; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_report_patent_base_application_year ON derived_layer.report_patent_base USING btree (application_year);

--
-- Name: idx_report_patent_base_country_code; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_report_patent_base_country_code ON derived_layer.report_patent_base USING btree (country_code);

--
-- Name: idx_report_patent_base_cpc_main; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_report_patent_base_cpc_main ON derived_layer.report_patent_base USING btree ("Curr. CPC(Main)");

--
-- Name: idx_report_patent_base_ipc_main; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_report_patent_base_ipc_main ON derived_layer.report_patent_base USING btree ("Curr. IPC(Main)");

--
-- Name: idx_report_patent_base_owner_display; Type: INDEX; Schema: derived_layer; Owner: -
--

CREATE INDEX idx_report_patent_base_owner_display ON derived_layer.report_patent_base USING btree (current_assignee_display_name);

--
-- Name: idx_raw_records_raw_data_gin; Type: INDEX; Schema: raw_layer; Owner: -
--

CREATE INDEX idx_raw_records_raw_data_gin ON raw_layer.raw_records USING gin (raw_data);

--
-- Name: idx_raw_records_source_file_id; Type: INDEX; Schema: raw_layer; Owner: -
--

CREATE INDEX idx_raw_records_source_file_id ON raw_layer.raw_records USING btree (source_file_id);

--
-- Name: idx_source_files_file_hash; Type: INDEX; Schema: raw_layer; Owner: -
--

CREATE INDEX idx_source_files_file_hash ON raw_layer.source_files USING btree (file_hash);

--
-- Name: idx_source_files_imported_at; Type: INDEX; Schema: raw_layer; Owner: -
--

CREATE INDEX idx_source_files_imported_at ON raw_layer.source_files USING btree (imported_at);

--
-- Name: analysis_outputs analysis_outputs_analysis_id_fkey; Type: FK CONSTRAINT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.analysis_outputs
    ADD CONSTRAINT analysis_outputs_analysis_id_fkey FOREIGN KEY (analysis_id) REFERENCES app_layer.analysis_runs(analysis_id) ON DELETE RESTRICT;

--
-- Name: export_runs export_runs_analysis_id_fkey; Type: FK CONSTRAINT; Schema: app_layer; Owner: -
--

ALTER TABLE ONLY app_layer.export_runs
    ADD CONSTRAINT export_runs_analysis_id_fkey FOREIGN KEY (analysis_id) REFERENCES app_layer.analysis_runs(analysis_id) ON DELETE RESTRICT;

--
-- Name: patent_attributes patent_attributes_patent_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_attributes
    ADD CONSTRAINT patent_attributes_patent_id_fkey FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

--
-- Name: patent_attributes patent_attributes_raw_record_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_attributes
    ADD CONSTRAINT patent_attributes_raw_record_id_fkey FOREIGN KEY (raw_record_id) REFERENCES raw_layer.raw_records(id) ON DELETE SET NULL;

--
-- Name: patent_attributes patent_attributes_source_file_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_attributes
    ADD CONSTRAINT patent_attributes_source_file_id_fkey FOREIGN KEY (source_file_id) REFERENCES raw_layer.source_files(id) ON DELETE SET NULL;

--
-- Name: patent_people patent_people_patent_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_people
    ADD CONSTRAINT patent_people_patent_id_fkey FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

--
-- Name: patent_sources patent_sources_patent_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_sources
    ADD CONSTRAINT patent_sources_patent_id_fkey FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

--
-- Name: patent_sources patent_sources_raw_record_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_sources
    ADD CONSTRAINT patent_sources_raw_record_id_fkey FOREIGN KEY (raw_record_id) REFERENCES raw_layer.raw_records(id) ON DELETE CASCADE;

--
-- Name: patent_sources patent_sources_source_file_id_fkey; Type: FK CONSTRAINT; Schema: core_layer; Owner: -
--

ALTER TABLE ONLY core_layer.patent_sources
    ADD CONSTRAINT patent_sources_source_file_id_fkey FOREIGN KEY (source_file_id) REFERENCES raw_layer.source_files(id) ON DELETE CASCADE;

--
-- Name: report_patent_base report_patent_base_patent_id_fkey; Type: FK CONSTRAINT; Schema: derived_layer; Owner: -
--

ALTER TABLE ONLY derived_layer.report_patent_base
    ADD CONSTRAINT report_patent_base_patent_id_fkey FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

--
-- Name: raw_records raw_records_source_file_id_fkey; Type: FK CONSTRAINT; Schema: raw_layer; Owner: -
--

ALTER TABLE ONLY raw_layer.raw_records
    ADD CONSTRAINT raw_records_source_file_id_fkey FOREIGN KEY (source_file_id) REFERENCES raw_layer.source_files(id) ON DELETE CASCADE;

--
-- PostgreSQL database dump complete
--
