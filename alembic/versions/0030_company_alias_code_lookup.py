"""company_aliases 唯一鍵改為 (申請人代碼, alias_lookup_key)

Revision ID: 0030_company_alias_code_lookup
Revises: 0029_report_base_orig_ipc_cpc
Create Date: 2026-07-23

2026-07-23 定案「申請人代碼是公司收斂的依據」的 DB 端：

舊索引 ux_company_aliases_lookup_confirmed 只約束 alias_lookup_key，
隱含假設「一個別稱字面全庫只能屬於一家公司」。改以代碼收斂後，
同一字面（如控股公司改名前後共用的簡稱）本來就可能分屬不同代碼，
唯一性層級應下放到「同一代碼內別稱不重複」。

- 新索引含「申請人代碼」→ 允許一別稱多公司（不同代碼），
  同代碼同別稱（normalize 後）仍唯一，收斂不會有兩列打架。
- 代碼為 NULL 的列（來源檔沒給代碼）在複合唯一索引下不互相衝突，
  這是 SQL NULL 語意；此類列本來就無法參與代碼收斂，交由別稱路徑處理。
- 仍只約束 review_status='confirmed'；review_required 可暫存歧義等人工裁決。
"""
from __future__ import annotations

from alembic import op


revision = "0030_company_alias_code_lookup"
down_revision = "0029_report_base_orig_ipc_cpc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """換成含代碼的複合唯一索引。"""
    # 先建新索引再刪舊的：若既有資料違反新約束會在此失敗，不會留下無保護的中間狀態。
    op.execute(
        """
        CREATE UNIQUE INDEX ux_company_aliases_code_lookup_confirmed
            ON derived_layer.company_aliases ("申請人代碼", alias_lookup_key)
            WHERE review_status = 'confirmed';
        """
    )
    op.execute("DROP INDEX IF EXISTS derived_layer.ux_company_aliases_lookup_confirmed;")


def downgrade() -> None:
    """還原 0013 的單欄唯一索引。

    注意：若 upgrade 後已寫入「同別稱跨代碼」的列，還原時會因違反舊約束而失敗，
    這是預期行為——資料已依新語意展開，不應靜默刪列來讓 downgrade 通過。
    """
    op.execute(
        """
        CREATE UNIQUE INDEX ux_company_aliases_lookup_confirmed
            ON derived_layer.company_aliases (alias_lookup_key)
            WHERE review_status = 'confirmed';
        """
    )
    op.execute("DROP INDEX IF EXISTS derived_layer.ux_company_aliases_code_lookup_confirmed;")
