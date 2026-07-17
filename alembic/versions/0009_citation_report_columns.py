"""citation and inventor-count columns for report layer

引用報表（高被引用排名、企業研發能量氣泡）與生命週期圖所需欄位：
report_patent_base 加三個整數欄，值來自 patent_attributes（完整欄位匯出才有）。
引用數是下載當下的快照值，與 EPC 欄同樣取「最新 raw_record」的那組，不混時點。

Revision ID: 0009_citation_report_cols
Revises: 0008_tw_number_columns
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0009_citation_report_cols"
down_revision = "0008_tw_number_columns"
branch_labels = None
depends_on = None

# 欄名照抄 patent_attributes 的繁體 display name，型別為 INTEGER（refresh 時已驗證數字才轉型）。
NEW_COLUMNS = (
    '"(F1)引用文獻數"',
    '"(B1)引用文獻數"',
    '"發明人數"',
)


def upgrade() -> None:
    for column in NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE derived_layer.report_patent_base ADD COLUMN IF NOT EXISTS {column} integer;"
        )


def downgrade() -> None:
    for column in reversed(NEW_COLUMNS):
        op.execute(
            f"ALTER TABLE derived_layer.report_patent_base DROP COLUMN IF EXISTS {column};"
        )
