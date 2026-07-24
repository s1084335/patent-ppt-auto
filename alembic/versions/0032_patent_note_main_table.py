"""把「文獻備註」欄從 core_layer.patent_attributes 搬到 core_layer.patents 主表。

Revision ID: 0032_patent_note_main_table
Revises: 0031_patent_figures_paired
Create Date: 2026-07-24

**為何搬主表（回寫可靠性）**：與 0026 主附圖搬主表同一模式。
`patent_attributes` 主鍵為 (patent_id, raw_record_id)，同一專利可有多列（多次匯入 raw_record）。
文獻備註為 AI 回寫欄，若落在 patent_attributes，回寫時必須先選出「該專利 raw_record_id 最大」
那一列再 UPDATE——這條路徑既可能寫到錯的列（若前端讀取規則與寫入規則不一致），也可能在該專利
尚無屬性列時 UPDATE 0 列而靜默成功（回寫失敗卻無錯）。改放 `patents`（一專利一列）後，回寫
直接 `UPDATE core_layer.patents SET "文獻備註" = %s WHERE id = %s`，保證命中且不選列，可靠。
（與 0026「主附圖搬主表」、0030 前例同一考量。）

**舊值不搬移**：文獻備註是 AI 產出欄，現正式庫該欄實務恆空（唯讀查證：patent_attributes 目前
0 列、"文獻備註" 非空 0 筆），無資料保留價值。故 upgrade 移除舊欄時不回搬舊值、downgrade
還原舊欄時亦不回搬——搬空欄沒有意義，且避免 raw_record 選列邏輯在 migration 內再現一次。

downgrade：反向——移除 patents."文獻備註"、還原 patent_attributes."文獻備註" TEXT（不搬值）。
"""
from __future__ import annotations

from alembic import op


revision = "0032_patent_note_main_table"
down_revision = "0031_patent_figures_paired"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # patents 主表新增文獻備註欄（一專利一列，回寫直接 WHERE id，不選 raw_record 列）。
    op.execute('ALTER TABLE core_layer.patents ADD COLUMN IF NOT EXISTS "文獻備註" TEXT')
    op.execute(
        'COMMENT ON COLUMN core_layer.patents."文獻備註" IS '
        "'AI 由專利獨立項（主權項）摘要而成的一段簡短文獻備註；一專利一列，回寫直接 WHERE id。"
        "來源欄從 patent_attributes 搬來（0032），舊落點實務恆空、未回搬舊值'"
    )
    # 移除舊落點：patent_attributes 的文獻備註欄（實務恆空，不回搬），避免雙落點。
    op.execute('ALTER TABLE core_layer.patent_attributes DROP COLUMN IF EXISTS "文獻備註"')


def downgrade() -> None:
    op.execute('ALTER TABLE core_layer.patents DROP COLUMN IF EXISTS "文獻備註"')
    # 還原舊欄型別 TEXT；原欄實務恆空，不回搬主表值。
    op.execute('ALTER TABLE core_layer.patent_attributes ADD COLUMN IF NOT EXISTS "文獻備註" TEXT')
