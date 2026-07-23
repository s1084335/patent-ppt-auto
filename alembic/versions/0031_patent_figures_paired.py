"""專利代表圖 (主附圖) 中期版增量：創建 patent_figures 表，兩階段存圖；主表欄變為最新版快取。

Revision ID: 0031_patent_figures_paired
Revises: 0030_company_alias_code_lookup
Create Date: 2026-07-23 15:30:00.000

國際標準文獻階段字類字形如 A = 早期公開，B = 審定公告。國內制度（TW 等）可能有 A1/A2/B1/B2 等，
但相同階段的字類皆與 A（前）/ B（後）同義；中間階段不存入。IBT 只存2-(document_kind)為 A/B 階段的圖。
"""
from __future__ import annotations

from alembic import op


revision = '0031_patent_figures_paired'
down_revision = '0030_company_alias_code_lookup'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. 中期版針對同一專利之主附圖一對多保存：(patent_id, document_kind)
    op.execute('''
        CREATE TABLE core_layer.patent_figures (
            patent_id      BIGINT NOT NULL REFERENCES core_layer.patents(id) ON DELETE CASCADE,
            document_kind  TEXT   NOT NULL,
            content        BYTEA  NOT NULL,
            PRIMARY KEY (patent_id, document_kind)
        );
    ''')
    op.execute('ALTER TABLE core_layer.patent_figures ALTER COLUMN content SET STORAGE EXTERNAL;')

    # 2. 回填：以已有主表「主附圖」為依據，一對應一列（document_kind 未知標 UNKNOWN）。
    op.execute('''
        INSERT INTO core_layer.patent_figures (patent_id, document_kind, content)
        SELECT id, COALESCE(document_kind, 'UNKNOWN'), "主附圖"
        FROM core_layer.patents
        WHERE "主附圖" IS NOT NULL;
    ''')


def downgrade() -> None:
    # 只還原本次變更（刪掉本 migration 建的表）。主表 "主附圖" 屬 0026 的既有欄位，
    # 其值在 downgrade 後回到短期版語意（每專利一張），仍可供前端顯示——
    # 清空它會破壞本次變更以外的既有資料，超出 downgrade 範圍，故不清值。
    op.execute('DROP TABLE IF EXISTS core_layer.patent_figures CASCADE;')
