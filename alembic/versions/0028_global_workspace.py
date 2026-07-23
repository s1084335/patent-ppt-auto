"""app_layer.workspaces 新增 is_global 旗標，標記唯一的「全庫 workspace」。

Revision ID: 0028_global_workspace
Revises: 0027_workspace_documents
Create Date: 2026-07-23

背景（2026-07-23 定案「專利總覽＝全庫 workspace」）：專利總覽是**跨 workspace** 的層級，
語意為「所有 workspace 專利的總和，一樣要能分群、跑報表」。實作方式是保留一個特殊
workspace，成員為全部專利——分群、報表、AI 全部沿用既有機制，架構完全不用改
（否決了讓 workspace_id 允許 NULL：所有查詢都要處理 NULL 分支，改動面大）。

**為何用專屬欄位而非 settings_json 標記**（使用者定案）：全庫 workspace **只能有一個**，
這個唯一性必須由資料庫保證。JSONB 內的標記無法（實務上）用簡潔的約束擋住第二筆，
只能靠程式判斷，一旦漏判就會出現兩個全庫、兩份 artifact，分群與報表全部分歧。改成
獨立 boolean 欄後可用 **partial unique index**（UNIQUE ... WHERE is_global）在 DB 層
強制唯一：程式漏判也只會拿到 UniqueViolation，不會產生第二個全庫。

partial index 而非一般 unique：一般 workspace 的 is_global 全為 false，若不加 WHERE
條件，false 之間會互相衝突而只允許一個非全庫 workspace。加了 WHERE is_global 後索引
只收錄 true 的那一列，非全庫 workspace 數量不受限。

欄位為 NOT NULL DEFAULT false：既有列自動補 false（無資料搬移需求），新建 workspace
不指定即為一般 workspace。全庫 workspace 由 app_layer/global_workspace.py 於第一次匯入
時自動建立，不在 migration 內預先塞資料（避免在沒有專利的空庫建出空 workspace）。

downgrade：移除 index 與欄位。全庫 workspace 本身是一般 workspace 的一列，降版後只是
失去「全庫」標記而退化為普通 workspace，成員與既有 artifact 都不受影響，故不做資料搬移。
"""
from __future__ import annotations

from alembic import op


revision = "0028_global_workspace"
down_revision = "0027_workspace_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app_layer.workspaces "
        "ADD COLUMN IF NOT EXISTS is_global BOOLEAN NOT NULL DEFAULT false"
    )
    # DB 層保證全庫 workspace 至多一個；partial 條件讓一般 workspace（false）不受約束。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_workspaces_is_global "
        "ON app_layer.workspaces (is_global) WHERE is_global"
    )
    op.execute(
        "COMMENT ON COLUMN app_layer.workspaces.is_global IS "
        "'true 表示此為唯一的全庫 workspace（專利總覽），成員涵蓋所有匯入專利、由匯入自動同步；"
        "唯一性由 partial unique index ux_workspaces_is_global 保證，不得刪除／改名／手動增減成員'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app_layer.ux_workspaces_is_global")
    op.execute("ALTER TABLE app_layer.workspaces DROP COLUMN IF EXISTS is_global")
