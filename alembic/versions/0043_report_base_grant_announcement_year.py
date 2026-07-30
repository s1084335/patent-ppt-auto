"""report_patent_base 補「授權公告年」供核准公告趨勢使用。

Revision ID: 0043_report_base_grant_announcement_year
Revises: 0042_applicant_expanded_view
Create Date: 2026-07-30

「授權公告年」只代表 WIPS「授權公告日」衍生年份，不得和
「未審查的公開日」或「審查的公告日」混用。既有 publication_year
保留為匯入階段的代表日期年，相容舊功能。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0043_report_base_grant_announcement_year"
down_revision = "0042_applicant_expanded_view"
branch_labels = None
depends_on = None


_VIEW_STAR = """
CREATE VIEW derived_layer.report_patent_base AS
SELECT * FROM legacy_0021.report_patent_base;
"""


_APPLICANT_EXPANDED_VIEW = """
CREATE OR REPLACE VIEW derived_layer.report_patent_applicant_expanded AS
WITH expanded AS (
    SELECT
        b.patent_id,
        b.application_year,
        b.publication_year,
        b."授權公告年",
        b.country_code,
        b."Orig. IPC(Main)",
        b."Orig. CPC(Main)",
        b.recent_assignee_display_name,
        BTRIM(part) AS raw_applicant,
        (BTRIM(part) = BTRIM(split_part(COALESCE(b."申請人", ''), '|', 1))) AS is_primary
    FROM derived_layer.report_patent_base b
    CROSS JOIN LATERAL regexp_split_to_table(
        COALESCE(NULLIF(BTRIM(b."申請人"), ''), b.applicant_display_name, ''),
        '\\s*\\|\\s*'
    ) AS part
    WHERE NULLIF(BTRIM(part), '') IS NOT NULL
)
SELECT
    e.patent_id,
    e.application_year,
    e.publication_year,
    e."授權公告年",
    e.country_code,
    e."Orig. IPC(Main)",
    e."Orig. CPC(Main)",
    e.recent_assignee_display_name,
    e.is_primary,
    COALESCE(
        NULLIF(BTRIM(ca."公司中文名稱"), ''),
        NULLIF(BTRIM(ca."正規化名稱"), ''),
        e.raw_applicant
    ) AS applicant_display_name
FROM expanded e
LEFT JOIN LATERAL (
    SELECT c."公司中文名稱", c."正規化名稱"
    FROM derived_layer.company_aliases c
    WHERE c.review_status = 'confirmed'
      AND lower(regexp_replace(BTRIM(c."別稱"), '\\s+', ' ', 'g'))
        = lower(regexp_replace(e.raw_applicant, '\\s+', ' ', 'g'))
    ORDER BY c.id
    LIMIT 1
) ca ON true
"""


_APPLICANT_EXPANDED_VIEW_DOWNGRADE = """
CREATE OR REPLACE VIEW derived_layer.report_patent_applicant_expanded AS
WITH expanded AS (
    SELECT
        b.patent_id,
        b.application_year,
        b.publication_year,
        b.country_code,
        b."Orig. IPC(Main)",
        b."Orig. CPC(Main)",
        b.recent_assignee_display_name,
        BTRIM(part) AS raw_applicant,
        (BTRIM(part) = BTRIM(split_part(COALESCE(b."申請人", ''), '|', 1))) AS is_primary
    FROM derived_layer.report_patent_base b
    CROSS JOIN LATERAL regexp_split_to_table(
        COALESCE(NULLIF(BTRIM(b."申請人"), ''), b.applicant_display_name, ''),
        '\\s*\\|\\s*'
    ) AS part
    WHERE NULLIF(BTRIM(part), '') IS NOT NULL
)
SELECT
    e.patent_id,
    e.application_year,
    e.publication_year,
    e.country_code,
    e."Orig. IPC(Main)",
    e."Orig. CPC(Main)",
    e.recent_assignee_display_name,
    e.is_primary,
    COALESCE(
        NULLIF(BTRIM(ca."公司中文名稱"), ''),
        NULLIF(BTRIM(ca."正規化名稱"), ''),
        e.raw_applicant
    ) AS applicant_display_name
FROM expanded e
LEFT JOIN LATERAL (
    SELECT c."公司中文名稱", c."正規化名稱"
    FROM derived_layer.company_aliases c
    WHERE c.review_status = 'confirmed'
      AND lower(regexp_replace(BTRIM(c."別稱"), '\\s+', ' ', 'g'))
        = lower(regexp_replace(e.raw_applicant, '\\s+', ' ', 'g'))
    ORDER BY c.id
    LIMIT 1
) ca ON true
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_applicant_expanded;")
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_base;")
    op.add_column(
        "report_patent_base",
        sa.Column("授權公告年", sa.Integer(), nullable=True),
        schema="legacy_0021",
    )
    op.execute(
        'COMMENT ON COLUMN legacy_0021.report_patent_base."授權公告年" IS '
        "'由 core_layer.patent_attributes「授權公告日」衍生；核准公告趨勢專用，不含公開日 fallback。'"
    )
    op.create_index(
        "idx_report_patent_base_grant_announcement_year",
        "report_patent_base",
        ["授權公告年"],
        schema="legacy_0021",
    )
    op.execute(_VIEW_STAR)
    op.execute(_APPLICANT_EXPANDED_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_applicant_expanded;")
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_base;")
    op.drop_index(
        "idx_report_patent_base_grant_announcement_year",
        table_name="report_patent_base",
        schema="legacy_0021",
    )
    op.drop_column("report_patent_base", "授權公告年", schema="legacy_0021")
    op.execute(_VIEW_STAR)
    op.execute(_APPLICANT_EXPANDED_VIEW_DOWNGRADE)
