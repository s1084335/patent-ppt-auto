"""不相干專利排除清單表：derived_layer.workspace_excluded_patents。

Revision ID: 0035_workspace_excluded_patents
Revises: 0034_market_doc_summary
Create Date: 2026-07-24

背景（2026-07-23 使用者定案，規格 irrelevant-patent-filter-spec.md 第 66-74 行）：
分群後每主題取 c-TF-IDF 最低 N 筆給 AI 讀，輔助使用者逐筆決定是否剔除。使用者決定
「不相干」後採「保留成員 ＋ 另記排除清單」——標記須留存、可追溯、可反悔，不直接移出
workspaces.patent_ids_json。

## 為何新增獨立表（不塞 workspaces.settings_json）
排除量無法預測（可能數百上千），若塞 settings_json，該欄每次查 workspace 都會被整包拉回，
排除量大時拖慢**所有** workspace 查詢。此為 0024 否決 request_json、0027 否決 settings_json
存 PDF 的同一判準：**熱路徑欄位不放不定量資料**。故另立 workspace 級的獨立表。

## 落點 derived_layer
排除清單是分群（derived）產物的下游決策，與 0034 market_documents／market_doc_summaries、
其他 derived 表同 layer；非 app_layer 的核心 workspace 熱路徑資料。

## 最小口徑欄位（能推導的不存，沿 import_blobs／workspace_documents 精簡口徑）
- workspace_id + patent_id：**複合 PK**——天然去重（同一 ws 同一專利只留一列），且直接表達
  「排除是 workspace 級、非專利級」（同一 patent_id 可在 A 被排除、在 B 與全庫照常）。
- reason：AI 理由／人工註記，可空（人工排除可不填）。
- excluded_at：排除時間，供追溯。
- ⚠ 不含 topic_key（規格「待評估」）：當時屬哪主題可由 topic_assignments 推導，不重複存。
- ⚠ 不含代理主鍵：複合 PK 已足夠，沿專案「能推導/複合鍵夠用就不加 surrogate id」慣例。

workspace FK ON DELETE CASCADE：workspace 刪除時排除紀錄一併清（與 0034 兩表一致）。
不對 patent_id 設 FK：與 0034／既有 derived 表一致，patent 生命週期由 core_layer 管，
排除清單不阻擋核心刪除；且剔除語意為「移出 workspace 分析」，非刪除專利。

downgrade：直接 DROP 該表（排除紀錄無跨版保留需求，可由使用者重跑篩選重建）。
"""
from __future__ import annotations

from alembic import op


revision = "0035_workspace_excluded_patents"
down_revision = "0034_market_doc_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE derived_layer.workspace_excluded_patents (
            workspace_id BIGINT NOT NULL
                REFERENCES app_layer.workspaces(workspace_id) ON DELETE CASCADE,
            patent_id    BIGINT NOT NULL,
            reason       TEXT,
            excluded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, patent_id)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE derived_layer.workspace_excluded_patents IS "
        "'不相干專利排除清單（workspace 級）：使用者判定不相干後回寫，分析用取成員扣除、"
        "顯示用取成員不扣。複合 PK (workspace_id, patent_id) 天然去重且表達排除為 workspace 級——"
        "同一 patent_id 可在 A 被排除、在 B 與全庫照常。獨立表而非 settings_json：排除量不定量，"
        "沿 0024/0027 熱路徑欄位不放不定量資料的判準'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.reason IS "
        "'排除理由（AI 判定說明或人工註記）；可空——人工排除可不填'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS derived_layer.workspace_excluded_patents")
