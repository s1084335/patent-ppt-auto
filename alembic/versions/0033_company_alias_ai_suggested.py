"""company_aliases 放行 review_status/source_type = 'ai_suggested'（AI 中文名草稿）

Revision ID: 0033_company_alias_ai_suggested
Revises: 0032_patent_note_main_table, fd301dee99c3
Create Date: 2026-07-24

decisions.md 2026-07-24「公司中文名由 AI 產草稿、沿用 company_aliases」的 DB 端：

三態沿用既有 company_aliases 欄位、不新增欄——
- 未判斷：canonical 無 CJK、無 curation 裁決列、無草稿列（govern_company_names 的 needs_zh_name）。
- AI 草稿待確認：新增一列 review_status='ai_suggested'、source_type='ai_suggested'，
  公司名稱＝中文名草稿（verdict='translated'）或原文（verdict='keep_original'），
  verdict 存 wips_metadata_json->'zh_name_verdict'。
- 已確認：apply_confirmed_display_names 寫 review_status='confirmed'（含保留原文裁決）。

0013 的兩個 CHECK constraint 只放行 confirmed/review_required 與 excel_seed/wips_lookup/manual，
故草稿列會撞 CheckViolation。本 migration 只擴充這兩個 constraint 的允許值集合，
不動索引、不動資料、不新增欄。

⚠ 草稿列（ai_suggested）不落在 ux_company_aliases_code_lookup_confirmed 唯一索引下
（該索引 WHERE review_status='confirmed'），故草稿不影響既有 confirmed 唯一性，
也天然不進 refresh 的 code_alias_names（只採 confirmed）——AI 草稿不進正式顯示欄。
"""
from __future__ import annotations

from alembic import op


# 兩個既有 head（0032 數字鏈、fd301dee99c3 的 sse trigger 分支）在此匯流成單一 head，
# 讓 alembic upgrade head 有唯一目標。本 revision 本身只擴充 company_aliases 的 CHECK。
revision = "0033_company_alias_ai_suggested"
down_revision = ("0032_patent_note_main_table", "fd301dee99c3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """把 'ai_suggested' 加入 review_status 與 source_type 的允許值。"""
    op.execute(
        """
        ALTER TABLE derived_layer.company_aliases
            DROP CONSTRAINT IF EXISTS company_aliases_review_status_check,
            ADD CONSTRAINT company_aliases_review_status_check
                CHECK (review_status = ANY (ARRAY[
                    'confirmed'::text, 'review_required'::text, 'ai_suggested'::text]));
        """
    )
    op.execute(
        """
        ALTER TABLE derived_layer.company_aliases
            DROP CONSTRAINT IF EXISTS company_aliases_source_type_check,
            ADD CONSTRAINT company_aliases_source_type_check
                CHECK (source_type = ANY (ARRAY[
                    'excel_seed'::text, 'wips_lookup'::text, 'manual'::text,
                    'ai_suggested'::text]));
        """
    )


def downgrade() -> None:
    """還原為 0013 的允許值集合。

    ⚠ 若已寫入 ai_suggested 草稿列，downgrade 會因違反舊約束而失敗——
    這是預期行為，不應靜默刪草稿列來讓 downgrade 通過。
    """
    op.execute(
        """
        ALTER TABLE derived_layer.company_aliases
            DROP CONSTRAINT IF EXISTS company_aliases_review_status_check,
            ADD CONSTRAINT company_aliases_review_status_check
                CHECK (review_status = ANY (ARRAY[
                    'confirmed'::text, 'review_required'::text]));
        """
    )
    op.execute(
        """
        ALTER TABLE derived_layer.company_aliases
            DROP CONSTRAINT IF EXISTS company_aliases_source_type_check,
            ADD CONSTRAINT company_aliases_source_type_check
                CHECK (source_type = ANY (ARRAY[
                    'excel_seed'::text, 'wips_lookup'::text, 'manual'::text]));
        """
    )
