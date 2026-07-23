"""app_layer.report_artifacts：報表產物的跨容器共享表。

Revision ID: 0025_report_artifacts
Revises: 0024_import_blobs
Create Date: 2026-07-23

背景：與 0024_import_blobs 同一個成因——Railway 上 worker 與 backend 是**不同容器**、
檔案系統不共享。worker 跑 report_generate 產 output/full_report_latest/<版本>/
（report_data.json ＋ 約 20 張 SVG ＋ index.html ＋ manifest），backend 的
/report-latest/content、/reports/versions、asset 端點全在另一個容器讀不到，導致報表
內嵌顯示、匯出工作台、AI 解讀與 PPT 產生器整條卡住。

為何**另立新表**而非塞 app_layer.workflow_outputs：workflow_outputs 是 JSONB 版本化
結構化結果，其 artifact_manifest_json 依 0021 契約**只描述**圖檔（artifact_key、hash），
不放內容。把 20 張 SVG 塞進 JSONB 需 base64（放大 33%）、需 JSONB parse，且該欄一取
就是整包——做不到 asset 端點「只取單張圖」。本表一檔一列，讀取端可單檔取回。

與 import_blobs 的差異：import_blobs 是**用完即刪**的上傳傳輸暫存；報表產物是**長生命
週期**的版本化產物（前端要能回看舊版本），故不共用同一張表。

欄位只留必要的：版本（＝輸出目錄名，與檔案系統落點同一套命名）、檔名、內容、
hash 與大小（完整性追溯）。PK (version, filename) 讓同版本重跑 upsert 覆蓋同名檔。
"""
from __future__ import annotations

from alembic import op


revision = "0025_report_artifacts"
down_revision = "0024_import_blobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app_layer.report_artifacts (
            version    TEXT NOT NULL,
            filename   TEXT NOT NULL,
            content    BYTEA NOT NULL,
            file_hash  TEXT NOT NULL,
            byte_size  BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (version, filename)
        )
        """
    )
    # SVG／JSON／HTML 都是純文字，TOAST 預設壓縮有實際效益（與 import_blobs 存已壓縮的
    # xlsx 不同），故此處**不**改 STORAGE EXTERNAL，留預設 EXTENDED。
    op.execute(
        "COMMENT ON TABLE app_layer.report_artifacts IS "
        "'報表產物的跨容器共享表（worker 寫、backend 讀）：Railway 上兩容器檔案系統不共享，"
        "改以共用 PostgreSQL 傳遞。一檔一列，讀取端可單檔取回，不必為一張圖撈整版'"
    )
    op.execute(
        "COMMENT ON COLUMN app_layer.report_artifacts.version IS "
        "'報表版本＝輸出目錄名（report_trial_/analysis_ 前綴＋時間戳），與檔案系統落點同一套命名'"
    )
    op.execute(
        "COMMENT ON COLUMN app_layer.report_artifacts.content IS "
        "'產物原始位元組（SVG／JSON／HTML）；asset 端點只取單列，不整版拉回'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_layer.report_artifacts")
