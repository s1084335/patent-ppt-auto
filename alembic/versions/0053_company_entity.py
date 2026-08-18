"""公司實體表與集團成員的參照完整性

根因：「公司」在資料模型裡不存在——代碼只是散在 `company_aliases` 裡的重複欄位，
沒有任何東西定義「這個代碼存在」。於是刪代碼會留下集團孤兒、轉正會漏更新集團成員，
兩者都不報錯。

⚠ 範圍刻意只做 `company_group_members` 那條外鍵（2026-08-18 使用者裁決「丙」）：
- 今晚實際踩到的兩個問題（孤兒、promote 漏更新）都由這條解決
- `company_aliases` 那條要動 12 處寫入點，效益低很多；別稱唯一性已由 0052 顧到

`ON DELETE RESTRICT`：把靜默的副作用變成明確的動作。刪代碼與改集團是兩件事，
CASCADE 會讓一個動作偷偷做兩件。
`ON UPDATE CASCADE`：轉正時集團成員自動跟著換——promote 漏更新的 bug 從此寫不出來。

Revision ID: 0053_company_entity
Revises: 0052_alias_lookup_single_code
Create Date: 2026-08-18
"""
from __future__ import annotations

from alembic import op

revision = "0053_company_entity"
down_revision = "0052_alias_lookup_single_code"
branch_labels = None
depends_on = None

_FK = "fk_company_group_members_company"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_layer.companies (
            company_code TEXT PRIMARY KEY,
            is_temp      BOOLEAN GENERATED ALWAYS AS
                             (company_code LIKE 'TEMP:%') STORED,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_companies_code_nonblank
                CHECK (NULLIF(BTRIM(company_code), '') IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        COMMENT ON TABLE derived_layer.companies IS
        '公司代碼的唯一登記處。只回答「這個代碼存在嗎」——中文名／正規化名仍由 '
        'company_aliases 每列攜帶，刻意不搬（搬動會牽動所有讀取路徑）。'
        """
    )
    # 回填：別稱表與集團成員表的全部代碼（集團成員理論上是子集，仍取聯集保險）
    op.execute(
        """
        INSERT INTO derived_layer.companies (company_code)
        SELECT DISTINCT "申請人代碼" FROM derived_layer.company_aliases
        WHERE NULLIF(BTRIM("申請人代碼"), '') IS NOT NULL
        UNION
        SELECT DISTINCT company_code FROM derived_layer.company_group_members
        WHERE NULLIF(BTRIM(company_code), '') IS NOT NULL
        ON CONFLICT (company_code) DO NOTHING
        """
    )
    op.execute(
        f"""
        ALTER TABLE derived_layer.company_group_members
            ADD CONSTRAINT {_FK}
            FOREIGN KEY (company_code)
            REFERENCES derived_layer.companies(company_code)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
        """
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE derived_layer.company_group_members DROP CONSTRAINT IF EXISTS {_FK}")
    op.execute("DROP TABLE IF EXISTS derived_layer.companies")
