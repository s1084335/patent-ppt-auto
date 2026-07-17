"""add adjacent transformed Taiwan patent number columns

Revision ID: 0008_tw_number_columns
Revises: 0007_reorder_embeddings
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0008_tw_number_columns"
down_revision = "0007_reorder_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """重建 core/report 兩表，加入相鄰 generated 欄並完整恢復 FK 與索引。"""
    op.execute(
        r'''
        CREATE TABLE core_layer.patents_with_transformed_numbers (
            id BIGINT NOT NULL DEFAULT nextval('core_layer.patents_id_seq'::regclass),
            "授權公告號" TEXT,
            "審查的公告號" TEXT,
            "未審查的公開號" TEXT,
            "未審查的公開號(轉換後)" TEXT GENERATED ALWAYS AS (
                CASE
                    WHEN UPPER(BTRIM(COALESCE(country_code, ''))) = 'TW'
                     AND "未審查的公開號" ~ '^[0-9]{4}'
                     AND SUBSTRING("未審查的公開號" FROM 1 FOR 4)::INTEGER BETWEEN 1912 AND 2910
                    THEN LPAD((SUBSTRING("未審查的公開號" FROM 1 FOR 4)::INTEGER - 1911)::TEXT, 3, '0')
                         || SUBSTRING("未審查的公開號" FROM 5)
                    ELSE "未審查的公開號"
                END
            ) STORED,
            "申請號" TEXT,
            "申請號(轉換後)" TEXT GENERATED ALWAYS AS (
                CASE
                    WHEN UPPER(BTRIM(COALESCE(country_code, ''))) = 'TW'
                     AND "申請號" ~ '^[0-9]{4}'
                     AND SUBSTRING("申請號" FROM 1 FOR 4)::INTEGER BETWEEN 1912 AND 2910
                    THEN LPAD((SUBSTRING("申請號" FROM 1 FOR 4)::INTEGER - 1911)::TEXT, 3, '0')
                         || SUBSTRING("申請號" FROM 5)
                    ELSE "申請號"
                END
            ) STORED,
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

        INSERT INTO core_layer.patents_with_transformed_numbers (
            id, "授權公告號", "審查的公告號", "未審查的公開號", "申請號",
            country_code, database_name, document_kind, patent_type,
            publication_date, publication_year, application_date, application_year,
            title, title_original, abstract, "權利要求的項數",
            "所有權利要求[JP,KR,CN]", "主權項", "主權項(原文)",
            "獨立項數量[KR,JP,US,CN,EP,IN]", "獨立項[KR,JP,US,CN,EP,IN]",
            "獨立項(原文)[KR,JP,CN,EP]", "Orig. CPC(Main)", "Orig. IPC(Main)",
            "Curr. CPC(Main)", "Curr. IPC(Main)", legal_status, "WIPS同族ID"
        )
        SELECT
            id, "授權公告號", "審查的公告號", "未審查的公開號", "申請號",
            country_code, database_name, document_kind, patent_type,
            publication_date, publication_year, application_date, application_year,
            title, title_original, abstract, "權利要求的項數",
            "所有權利要求[JP,KR,CN]", "主權項", "主權項(原文)",
            "獨立項數量[KR,JP,US,CN,EP,IN]", "獨立項[KR,JP,US,CN,EP,IN]",
            "獨立項(原文)[KR,JP,CN,EP]", "Orig. CPC(Main)", "Orig. IPC(Main)",
            "Curr. CPC(Main)", "Curr. IPC(Main)", legal_status, "WIPS同族ID"
        FROM core_layer.patents
        ORDER BY id;

        CREATE TABLE derived_layer.report_patent_base_with_transformed_numbers (
            patent_id BIGINT NOT NULL,
            dedupe_key TEXT,
            "授權公告號" TEXT,
            "審查的公告號" TEXT,
            "未審查的公開號" TEXT,
            "未審查的公開號(轉換後)" TEXT GENERATED ALWAYS AS (
                CASE
                    WHEN UPPER(BTRIM(COALESCE(country_code, ''))) = 'TW'
                     AND "未審查的公開號" ~ '^[0-9]{4}'
                     AND SUBSTRING("未審查的公開號" FROM 1 FOR 4)::INTEGER BETWEEN 1912 AND 2910
                    THEN LPAD((SUBSTRING("未審查的公開號" FROM 1 FOR 4)::INTEGER - 1911)::TEXT, 3, '0')
                         || SUBSTRING("未審查的公開號" FROM 5)
                    ELSE "未審查的公開號"
                END
            ) STORED,
            "申請號" TEXT,
            "申請號(轉換後)" TEXT GENERATED ALWAYS AS (
                CASE
                    WHEN UPPER(BTRIM(COALESCE(country_code, ''))) = 'TW'
                     AND "申請號" ~ '^[0-9]{4}'
                     AND SUBSTRING("申請號" FROM 1 FOR 4)::INTEGER BETWEEN 1912 AND 2910
                    THEN LPAD((SUBSTRING("申請號" FROM 1 FOR 4)::INTEGER - 1911)::TEXT, 3, '0')
                         || SUBSTRING("申請號" FROM 5)
                    ELSE "申請號"
                END
            ) STORED,
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
            "比對用權利要求" TEXT,
            "WIPS同族ID" TEXT,
            legal_status TEXT,
            "WIPS同族各國家文獻數量(申請為準)" TEXT,
            "EPC有效國家[EP]" TEXT,
            "EPC無效國家[EP]" TEXT
        );

        INSERT INTO derived_layer.report_patent_base_with_transformed_numbers (
            patent_id, dedupe_key, "授權公告號", "審查的公告號",
            "未審查的公開號", "申請號", country_code,
            application_date, application_year, publication_year, title,
            "Curr. IPC(Main)", "Curr. CPC(Main)", "申請人", "申請人國籍",
            "標準化申請人", applicant_display_name, "發明人", "發明人國籍",
            "最近專利權人[US,JP,KR,CN,CA,AU]",
            "標準當前專利權人[US,JP,KR,CN,CA,AU]",
            current_assignee_display_name, "最近受讓人[US,KR,CN]",
            recent_assignee_display_name, "主權項", "獨立項[KR,JP,US,CN,EP,IN]",
            "所有權利要求[JP,KR,CN]", "比對用權利要求", "WIPS同族ID",
            legal_status, "WIPS同族各國家文獻數量(申請為準)",
            "EPC有效國家[EP]", "EPC無效國家[EP]"
        )
        SELECT
            patent_id, dedupe_key, "授權公告號", "審查的公告號",
            "未審查的公開號", "申請號", country_code,
            application_date, application_year, publication_year, title,
            "Curr. IPC(Main)", "Curr. CPC(Main)", "申請人", "申請人國籍",
            "標準化申請人", applicant_display_name, "發明人", "發明人國籍",
            "最近專利權人[US,JP,KR,CN,CA,AU]",
            "標準當前專利權人[US,JP,KR,CN,CA,AU]",
            current_assignee_display_name, "最近受讓人[US,KR,CN]",
            recent_assignee_display_name, "主權項", "獨立項[KR,JP,US,CN,EP,IN]",
            "所有權利要求[JP,KR,CN]", "比對用權利要求", "WIPS同族ID",
            legal_status, "WIPS同族各國家文獻數量(申請為準)",
            "EPC有效國家[EP]", "EPC無效國家[EP]"
        FROM derived_layer.report_patent_base
        ORDER BY patent_id;
        '''
    )
    op.execute(
        r'''
        DO $$
        BEGIN
            IF (SELECT count(*) FROM core_layer.patents)
               <> (SELECT count(*) FROM core_layer.patents_with_transformed_numbers) THEN
                RAISE EXCEPTION 'core patent row count changed during 0008';
            END IF;
            IF EXISTS (
                SELECT id, "授權公告號", "審查的公告號", "未審查的公開號", "申請號",
                       country_code, database_name, document_kind, patent_type,
                       publication_date, publication_year, application_date, application_year,
                       title, title_original, abstract, "權利要求的項數",
                       "所有權利要求[JP,KR,CN]", "主權項", "主權項(原文)",
                       "獨立項數量[KR,JP,US,CN,EP,IN]", "獨立項[KR,JP,US,CN,EP,IN]",
                       "獨立項(原文)[KR,JP,CN,EP]", "Orig. CPC(Main)", "Orig. IPC(Main)",
                       "Curr. CPC(Main)", "Curr. IPC(Main)", legal_status, "WIPS同族ID"
                FROM core_layer.patents
                EXCEPT
                SELECT id, "授權公告號", "審查的公告號", "未審查的公開號", "申請號",
                       country_code, database_name, document_kind, patent_type,
                       publication_date, publication_year, application_date, application_year,
                       title, title_original, abstract, "權利要求的項數",
                       "所有權利要求[JP,KR,CN]", "主權項", "主權項(原文)",
                       "獨立項數量[KR,JP,US,CN,EP,IN]", "獨立項[KR,JP,US,CN,EP,IN]",
                       "獨立項(原文)[KR,JP,CN,EP]", "Orig. CPC(Main)", "Orig. IPC(Main)",
                       "Curr. CPC(Main)", "Curr. IPC(Main)", legal_status, "WIPS同族ID"
                FROM core_layer.patents_with_transformed_numbers
            ) THEN
                RAISE EXCEPTION 'core patent values changed during 0008';
            END IF;
            IF (SELECT count(*) FROM derived_layer.report_patent_base)
               <> (SELECT count(*) FROM derived_layer.report_patent_base_with_transformed_numbers) THEN
                RAISE EXCEPTION 'report patent row count changed during 0008';
            END IF;
        END $$;

        ALTER TABLE app_layer.workspace_patents DROP CONSTRAINT workspace_patents_patent_id_fkey;
        ALTER TABLE core_layer.patent_attributes DROP CONSTRAINT patent_attributes_patent_id_fkey;
        ALTER TABLE core_layer.patent_embeddings DROP CONSTRAINT patent_embeddings_patent_id_fkey;
        ALTER TABLE core_layer.patent_people DROP CONSTRAINT patent_people_patent_id_fkey;
        ALTER TABLE core_layer.patent_sources DROP CONSTRAINT patent_sources_patent_id_fkey;
        ALTER TABLE derived_layer.topic_assignments DROP CONSTRAINT topic_assignments_patent_id_fkey;

        DROP TABLE derived_layer.report_patent_base;
        ALTER SEQUENCE core_layer.patents_id_seq OWNED BY NONE;
        DROP TABLE core_layer.patents;
        ALTER TABLE core_layer.patents_with_transformed_numbers RENAME TO patents;
        ALTER SEQUENCE core_layer.patents_id_seq OWNED BY core_layer.patents.id;
        ALTER TABLE core_layer.patents ADD CONSTRAINT patents_pkey PRIMARY KEY (id);

        SELECT setval(
            'core_layer.patents_id_seq',
            COALESCE((SELECT max(id) FROM core_layer.patents), 1),
            EXISTS (SELECT 1 FROM core_layer.patents)
        );

        CREATE INDEX idx_patents_official_publication_number ON core_layer.patents("授權公告號");
        CREATE INDEX idx_patents_examined_publication_number ON core_layer.patents("審查的公告號");
        CREATE INDEX idx_patents_publication_number ON core_layer.patents("未審查的公開號(轉換後)");
        CREATE INDEX idx_patents_application_number ON core_layer.patents("申請號(轉換後)");
        CREATE INDEX idx_patents_publication_year ON core_layer.patents(publication_year);
        CREATE INDEX idx_patents_application_year ON core_layer.patents(application_year);
        CREATE INDEX idx_patents_country_code ON core_layer.patents(country_code);

        ALTER TABLE app_layer.workspace_patents
            ADD CONSTRAINT workspace_patents_patent_id_fkey
            FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;
        ALTER TABLE core_layer.patent_attributes
            ADD CONSTRAINT patent_attributes_patent_id_fkey
            FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;
        ALTER TABLE core_layer.patent_embeddings
            ADD CONSTRAINT patent_embeddings_patent_id_fkey
            FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;
        ALTER TABLE core_layer.patent_people
            ADD CONSTRAINT patent_people_patent_id_fkey
            FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;
        ALTER TABLE core_layer.patent_sources
            ADD CONSTRAINT patent_sources_patent_id_fkey
            FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;
        ALTER TABLE derived_layer.topic_assignments
            ADD CONSTRAINT topic_assignments_patent_id_fkey
            FOREIGN KEY (patent_id) REFERENCES core_layer.patents(id) ON DELETE CASCADE;

        ALTER TABLE derived_layer.report_patent_base_with_transformed_numbers
            RENAME TO report_patent_base;
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
        '''
    )


def downgrade() -> None:
    """欄位重建不可無損降版，需由 0008 前正式備份復原。"""
    raise RuntimeError(
        "0008 adds generated business identifiers and changes downstream identity semantics; "
        "restore the pre-0008 full backup instead"
    )
