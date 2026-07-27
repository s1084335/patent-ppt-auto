"""排除清單複核狀態：workspace_excluded_patents 加 status / source / ai_verdict。

Revision ID: 0036_exclusion_review_status
Revises: 0035_workspace_excluded_patents
Create Date: 2026-07-27

背景（2026-07-27 使用者定案）：
`ai:irrelevant_filter` 改為**手動觸發**（移除分群完成後自動排程），AI 逐筆判讀後由使用者
以「保留／確定」裁決——保留＝留在原主題，確定＝歸到「不相干」。同時移除 UNCLASSIFIED／
OTHER 兩個空系統桶，人工剔除與 AI 確定的專利統一在「不相干」桶呈現。

## 為何擴充既有表，不另開待複核表
「保留」也是使用者的決定，必須留存——否則每次重跑 AI 判讀都要重新複核同一批專利。
待複核與已排除是**同一實體的兩種狀態**（同一 workspace 同一專利只會有一個結論），複合 PK
(workspace_id, patent_id) 已天然表達此唯一性；另開表反而要處理兩表同步與雙寫去重。
沿 0035「排除是 workspace 級」的設計，狀態欄直接掛在同一列上。

## 欄位
- status：'pending'（AI 判讀草稿，待人工裁決）｜'excluded'（已確定排除）。
  預設 'excluded'——既有列與人工剔除路徑不帶此欄寫入時，語意與 0035 完全一致。
  **只有 'excluded' 會被分群成員子查詢扣除**；'pending' 不影響任何分析結果。
  這是「AI 只輔助、不決定正式資料」的護欄：AI 寫得進 pending，寫不進 excluded。
- source：'manual'（人工剔除）｜'ai'（AI 判讀產生）。預設 'manual'，同上理由。
  保留裁決不刪列而是刪除該列（見下），故無 'kept' 狀態——保留＝不在排除清單上。
- ai_verdict：AI 原始判定字串，可空。與人工裁決結果（status）分欄存放，符合
  workflows.md「AI 原始輸出與人工覆核分欄」的結構型產出規範。
- reason 沿用 0035 既有欄（AI 理由與人工註記共用），不另開 ai_reason——同一語意不重複建欄。

## 「保留」為何是刪列而非第三種 status
保留＝該專利不屬於排除清單，語意上就是「不在表內」。若另立 status='kept' 保留在表中，
每個查排除清單的地方都要多一個過濾條件，且與複合 PK「一列＝一個排除決定」的語意衝突。
重跑 AI 判讀時以 NOT EXISTS 跳過已有結論者，即可達成「不重複複核」。

## 部分索引
idx_workspace_excluded_patents_pending：WHERE status='pending' 的部分索引，
供「列出某 workspace 的待複核清單」走索引。已排除者量大但查詢走複合 PK，不需另建。

downgrade：DROP 三欄與索引，保留原表與原資料（reason／excluded_at 不動）。
pending 列在 downgrade 後會退化為已排除列——這是唯一的語意損失，已於契約測試標注；
實務上 downgrade 前應先清空 pending（未裁決的草稿本就可由重跑 AI 重建）。
"""
from __future__ import annotations

from alembic import op


revision = "0036_exclusion_review_status"
down_revision = "0035_workspace_excluded_patents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 三欄一次加齊。DEFAULT 讓既有列自動 backfill（PG 11+ 不重寫全表），
    # NOT NULL 由 DEFAULT 保證，無須分兩步。
    op.execute(
        """
        ALTER TABLE derived_layer.workspace_excluded_patents
            ADD COLUMN status     TEXT NOT NULL DEFAULT 'excluded',
            ADD COLUMN source     TEXT NOT NULL DEFAULT 'manual',
            ADD COLUMN ai_verdict TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE derived_layer.workspace_excluded_patents
            ADD CONSTRAINT workspace_excluded_patents_status_check
                CHECK (status IN ('pending', 'excluded')),
            ADD CONSTRAINT workspace_excluded_patents_source_check
                CHECK (source IN ('manual', 'ai'))
        """
    )
    op.execute(
        """
        CREATE INDEX idx_workspace_excluded_patents_pending
            ON derived_layer.workspace_excluded_patents (workspace_id)
            WHERE status = 'pending'
        """
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.status IS "
        "'複核狀態：pending＝AI 判讀草稿待人工裁決（不影響任何分析）；"
        "excluded＝已確定排除（分群成員子查詢扣除）。預設 excluded 使人工剔除語意與 0035 一致。"
        "AI 只寫得進 pending，寫不進 excluded——AI 不決定正式資料'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.source IS "
        "'來源：manual＝人工剔除；ai＝ai:irrelevant_filter 判讀產生。兩者最終都在「不相干」桶呈現'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.ai_verdict IS "
        "'AI 原始判定字串，可空（人工剔除無）。與人工裁決結果（status）分欄存放'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS derived_layer.idx_workspace_excluded_patents_pending")
    op.execute(
        """
        ALTER TABLE derived_layer.workspace_excluded_patents
            DROP CONSTRAINT IF EXISTS workspace_excluded_patents_status_check,
            DROP CONSTRAINT IF EXISTS workspace_excluded_patents_source_check,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS ai_verdict
        """
    )
