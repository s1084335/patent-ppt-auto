"""report_patent_base 加 abstract 欄（2026-07-28）——文獻備註第三級來源。

## 為什麼

備註三級順位（使用者定案）：獨立項 → 所有權利要求 → abstract。
第三級是全類型保底：CN 外觀設計 11 筆的權利要求四欄全空（專利類型本質，
洛迦諾分類 21-02／19-07），但摘要 11/11、最長 530 字。沒有它那批專利
永遠沒備註，而備註正是它們交給 AI 補分的唯一輸入。

core_layer.patents 早有 abstract（60/60），derived 寬表沒搬——與同日
0038 的 Curr. IPC/CPC 同型的「core 有、derived 沒搬」斷點。

## VIEW 改 SELECT *

derived_layer.report_patent_base 原本逐欄列舉，導致**每次實體表加欄都要改
migration 重建 VIEW**，漏了就報表端讀不到（本專案已因此踩過數次）。
既然它就是實體表的相容別名、欄位一對一，改為 SELECT * 讓日後加欄自動生效。

⚠ 代價：欄位順序與名稱完全跟隨實體表，不能再靠 VIEW 做欄位改名或裁切。
目前沒有這種需求（它純粹是 0021 遷移留下的相容層）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0039_report_base_abstract"
down_revision = "0038_family_status_counts"
branch_labels = None
depends_on = None


_VIEW_STAR = """
CREATE VIEW derived_layer.report_patent_base AS
SELECT * FROM legacy_0021.report_patent_base;
"""


def upgrade() -> None:
    op.add_column(
        "report_patent_base",
        sa.Column("abstract", sa.Text(), nullable=True),
        schema="legacy_0021",
    )
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_base;")
    op.execute(_VIEW_STAR)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_base;")
    op.drop_column("report_patent_base", "abstract", schema="legacy_0021")
    # 回退後仍用 SELECT *（欄位少一個 abstract，其餘不變）——不還原逐欄列舉，
    # 那正是本次要消除的維護負擔。
    op.execute(_VIEW_STAR)
