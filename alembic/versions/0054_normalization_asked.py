"""正規化候選的「問過了」紀錄

根因：候選是每次即時算出來的（`core_layer.patent_people` → 名稱正規化 → md5），
不是資料列，所以「被問過」無處可蓋章——查不到證據的候選每跑一次就再燒一次。
實測 #411 有 7 個沒結論，#416 就原封不動重問那 7 個，263 秒換到 1 筆。

⚠ 本表**只存候選查詢產出的 lookup_key**，不自己算。再寫一次
`lower(regexp_replace(...))` 就會有第二份定義，兩份會各自演進而不報錯。

`asked_patent_count` NOT NULL：重新入列的規則是「件數比查證當時多」
（2026-08-18 使用者裁決「乙」）。可空的話比較會變成三值邏輯而靜默失效。

`outcome` 本輪不參與判斷，但必須存——「查無證據」與「有建議但沒被確認」
在畫面上長得一樣，不分開事後查不出來。

Revision ID: 0054_normalization_asked
Revises: 0053_company_entity
Create Date: 2026-08-18
"""
from __future__ import annotations

from alembic import op

revision = "0054_normalization_asked"
down_revision = "0053_company_entity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_layer.company_normalization_asked (
            lookup_key         TEXT PRIMARY KEY,
            last_asked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_run_id        BIGINT,
            asked_patent_count INTEGER NOT NULL,
            outcome            TEXT NOT NULL,
            CONSTRAINT ck_normalization_asked_outcome
                CHECK (outcome IN ('suggested', 'no_evidence'))
        )
        """
    )
    op.execute(
        """
        COMMENT ON TABLE derived_layer.company_normalization_asked IS
        '正規化候選的查證紀錄。候選本身是即時算出來的，沒有實體列可蓋章；'
        '本表就是那個章。lookup_key 一律由候選查詢產出後原樣寫回，不在此重算。'
        """
    )
    # 排隊用：ORDER BY last_asked_at NULLS FIRST 走這條
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_normalization_asked_last_asked
            ON derived_layer.company_normalization_asked (last_asked_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS derived_layer.company_normalization_asked")
