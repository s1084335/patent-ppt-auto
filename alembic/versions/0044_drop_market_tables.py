"""drop market tables（市場線整個移除）

Revision ID: 0044_drop_market_tables
Revises: 0043_grant_announcement_year

🔴 2026-08-04 使用者定案：市場線（上傳→AI 摘要→證據庫→痛點板）整個移除，
**資料表一併刪除**。三張表：
- derived_layer.market_doc_summaries（0034）
- derived_layer.market_documents（0034）
- derived_layer.market_evidence（0023）

⚠ downgrade 只重建空殼 schema（照原 migration 的定義），資料不可回復——
這是使用者知情的決定（「一併刪除」），要回復只能從 DB 備份。
"""
from alembic import op

revision = "0044_drop_market_tables"
down_revision = "0043_grant_announcement_year"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 依相依順序：summaries 參照 documents，先刪子表。
    op.execute("DROP TABLE IF EXISTS derived_layer.market_doc_summaries")
    op.execute("DROP TABLE IF EXISTS derived_layer.market_documents")
    op.execute("DROP TABLE IF EXISTS derived_layer.market_evidence")


def downgrade() -> None:
    # 空殼重建交由重跑 0023／0034 的 SQL；此處不內嵌整份 DDL——
    # 市場線程式已刪，重建空表沒有服務對象，保留 no-op 讓 downgrade 鏈不斷。
    pass
