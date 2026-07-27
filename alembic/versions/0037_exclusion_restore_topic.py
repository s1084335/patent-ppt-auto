"""排除復原：workspace_excluded_patents 加 restored_topic_key（原主題快照）。

Revision ID: 0037_exclusion_restore_topic
Revises: 0036_exclusion_review_status
Create Date: 2026-07-27

背景（2026-07-27 使用者要求：「也要有個再加回原主題機制，預防使用者後悔」）：
0035 的設計說明寫「標記須留存、可追溯、**可反悔**」，但確定排除時會
`DELETE topic_assignments`（落實「移出該 workspace 分析」），而排除表**不記原主題**——
反悔後根本回不到原本的主題。可反悔在當時只做到「知道曾被排除」，沒做到「放得回去」。

## 為何補這一欄（0035 曾把 topic_key 列為「待評估」）
0035 的理由是「當時屬哪主題可由 topic_assignments 推導，不重複存」。該推導前提在
「排除會刪掉 assignment」的行為下**不成立**——推導來源自己被刪了。需求出現，故補。

## 為何不用其他兩種放回方式
- **重算最近主題**：主題被合併／停用後會算到別處，且需載 embeddings 與 artifact，
  成本高（artifact 動輒數十 MB，且在容器裡、Companion 在使用者本機）。
- **只從清單移除、不指派**：「未分類」「其他」系統桶已於 2026-07-27 移除，
  沒有主題的專利會從主題視圖消失——等於換一種方式不見。

## 欄型 JSONB 而非 TEXT
一筆專利在**技術與功效兩個通道各有一筆 assignment**，topic_code 可能不同，
且未來通道數可能增加（source_fields 為白名單常數，非寫死兩個）。
故存 `[{"run_id": 12, "topic_key": "T002", "distance": 0.42}, ...]` 的陣列，
放回時逐筆還原、含原 distance_to_centroid（**不重算**——沿「剔除不重跑分群」的精神，
放回同樣不重跑）。可空：pending 列與升級前的既有列都沒有這份快照。

downgrade：直接 DROP 該欄。已排除者將失去還原資訊（放回後不會回到原主題），
屬可接受的降級損失——排除本身與 reason 都還在。
"""
from __future__ import annotations

from alembic import op


revision = "0037_exclusion_restore_topic"
down_revision = "0036_exclusion_review_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE derived_layer.workspace_excluded_patents
            ADD COLUMN restored_topic_key JSONB
        """
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.restored_topic_key IS "
        "'排除當下的主題指派快照，供「放回原主題」還原，形如 "
        "[{run_id, topic_key, distance}, ...]。一筆專利在技術／功效各通道各有一筆，"
        "故為陣列。放回時逐筆還原含原 distance_to_centroid，不重算、不重跑分群。"
        "可空——pending 列與 0037 之前的既有列沒有此快照'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP COLUMN IF EXISTS restored_topic_key"
    )
