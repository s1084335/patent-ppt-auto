"""family country layout report tables and columns

國家佈局報表（現有保護口徑）所需 schema：
1. report_patent_base 加 5 欄（同族/狀態/EPC），來源為 core_layer.patents 與 patent_attributes。
2. 新表 report_family_country：家族×國家 一列，佈局統計來源（group by country_code 即得各國家族數）。
3. 新表 report_family_quality：每家族一列，完整性核對與異常現形（不完整/生效程序進行中/unknown 狀態等）。

Revision ID: 0005_family_country_layout
Revises: 0004_clustering_tables
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0005_family_country_layout"
down_revision = "0004_clustering_tables"
branch_labels = None
depends_on = None

# 欄名一律照抄 0001_baseline_schema.sql 的實際欄名（繁體 display name，
# 注意「申請為準」是繁體、半形括號），不可手打改寫。
REPORT_BASE_NEW_COLUMNS = (
    '"WIPS同族ID" text',
    "legal_status text",
    '"WIPS同族各國家文獻數量(申請為準)" text',
    '"EPC有效國家[EP]" text',
    '"EPC無效國家[EP]" text',
)


def upgrade() -> None:
    for column_def in REPORT_BASE_NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE derived_layer.report_patent_base ADD COLUMN IF NOT EXISTS {column_def};"
        )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_layer.report_family_country (
            family_id TEXT NOT NULL,
            country_code TEXT NOT NULL,
            -- 非 EP 存活件直接貢獻的件數（同家族同國多件仍一列，件數累計於此）
            direct_patent_count INTEGER NOT NULL DEFAULT 0,
            -- 經 EP 生效國展開貢獻的 EP 件數
            via_ep_count INTEGER NOT NULL DEFAULT 0,
            -- 反正規化的家族完整性 flag，方便報表直接現形（詳細見 report_family_quality）
            family_incomplete BOOLEAN NOT NULL DEFAULT FALSE,
            -- WIPS同族ID 為空時以單件家族（surrogate id）代替，不無聲丟掉真實保護
            is_surrogate_family BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (family_id, country_code)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_report_family_country_country
            ON derived_layer.report_family_country (country_code);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_layer.report_family_quality (
            family_id TEXT PRIMARY KEY,
            is_surrogate_family BOOLEAN NOT NULL DEFAULT FALSE,
            -- 本庫實際撈到的家族成員列數
            member_rows INTEGER NOT NULL,
            -- 原始同族明細字串（如 "US-2 | EP-1 | PCT-0 | JP-0 | KR-0 | CN-0 | etc-1"）
            expected_counts_raw TEXT,
            -- 明細件數 vs 實際撈到列數對不上（只比 US/EP/JP/KR/CN 五桶；PCT/etc 不比）
            family_incomplete BOOLEAN NOT NULL DEFAULT FALSE,
            incomplete_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            -- 各種異常計數：現形而非吞掉
            unknown_status_count INTEGER NOT NULL DEFAULT 0,
            pending_status_count INTEGER NOT NULL DEFAULT 0,
            -- 規則②：剛授權 EP（無效國空 且 有效國數 >= 30）隔離為「生效程序進行中」
            ep_in_transition_count INTEGER NOT NULL DEFAULT 0,
            -- EP 存活但 EPC 兩欄皆空白（最新來源是精簡匯出時會發生，需現形）
            ep_missing_epc_count INTEGER NOT NULL DEFAULT 0,
            -- country_code 非國家（WO 等受理局）的列數
            non_country_row_count INTEGER NOT NULL DEFAULT 0,
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS derived_layer.report_family_quality;")
    op.execute("DROP TABLE IF EXISTS derived_layer.report_family_country;")
    for column_def in reversed(REPORT_BASE_NEW_COLUMNS):
        column_name = column_def.rsplit(" ", 1)[0]
        op.execute(
            f"ALTER TABLE derived_layer.report_patent_base DROP COLUMN IF EXISTS {column_name};"
        )
