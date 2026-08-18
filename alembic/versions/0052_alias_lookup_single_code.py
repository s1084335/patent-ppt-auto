"""一個別稱只屬於一個公司代碼（partial unique index）

⚠ 判準落在**別稱**層不是代碼層：一家公司擁有多個 WIPS 代碼是常態
（創科集團有三個代碼，由 company_groups 收攏），擋它會破壞合法的集團結構。
不合法的是「同一個法人名字對到兩個法人代碼」——那讓歸戶取決於查詢順序。

⚠ partial（只管 confirmed）：AI 建議與待審草稿允許暫時重複，
使用者確認時才會被擋；被擋是 index 正確工作，不是新 bug。

2026-08-18 實例：`TTI (MACAO COMMERCIAL OFFSHORE) Ltd.` 同時掛在
UN164421（Techtronic Industries）與 UN240278（Chuang Ke Macao）下，
WIPS 權威資料確認只屬後者。

Revision ID: 0052_alias_lookup_single_code
Revises: 0051_patent_search_terms
Create Date: 2026-08-18
"""
from __future__ import annotations

from alembic import op

revision = "0052_alias_lookup_single_code"
down_revision = "0051_patent_search_terms"
branch_labels = None
depends_on = None

_INDEX = "ux_company_aliases_lookup_single_code"


def upgrade() -> None:
    # ⚠ 既有的 ux_company_aliases_code_lookup_confirmed 是 (代碼, 別稱) 複合鍵，
    #   代碼在鍵裡，所以「同一別稱、不同代碼」是被允許的——本 index 補上那個缺口。
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX}
            ON derived_layer.company_aliases (alias_lookup_key)
            WHERE review_status = 'confirmed' AND alias_lookup_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS derived_layer.{_INDEX}")
