"""core_layer.patents 新增 "主附圖" bytea（WIPS Excel 內嵌專利代表圖）。

Revision ID: 0026_patent_main_figure
Revises: 0025_report_artifacts
Create Date: 2026-07-23

背景：WIPS Excel 匯出的「主附圖」欄**不是儲存格文字**，而是錨在該列的**浮動圖片物件**
（openpyxl `ws._images`，儲存格值只有一個空白字元）。舊 schema 把該欄當一般屬性欄
（core_layer.patent_attributes."主附圖" TEXT）匯入，實際只存到空白字串，等於整批代表圖
全數遺失。本版把它改成能真正保存圖片位元組的欄位。

**為何存 DB 而非檔案系統**：與 0024 import_blobs 同一個既有約束——Railway 上 backend 與
worker 是不同容器、檔案系統不共享（volume 只能綁單一服務），唯一共用的持久層就是
PostgreSQL。代表圖要同時被匯入端寫、Web 端讀、未來報表／PPT 產生器取用，放本機磁碟在
部署環境必然取不到。單張平均約 16KB（長邊 460px JPEG），一批 1900 筆約 30MB，屬 bytea
可接受量級。

**為何放 core_layer.patents 主表而非 patent_attributes**：
1. 代表圖是**專利本身的一對一屬性**，與標題／摘要同層級，不是可增減的來源欄位集合；
   patent_attributes 為「raw_record 逐列快照」（含 raw_record_id、匯入時整列 DELETE 後重寫），
   語意上是來源側資料，圖片放那裡會隨每次重匯反覆搬移數十 MB。
2. patent_attributes 的欄位是 mapping 自動推導的 TEXT 欄，型別上就放不了 bytea。
3. 已確認 core_layer.patents **沒有任何 `SELECT *` 查詢**（既有 14 處引用皆列名取欄），
   故加一個大 bytea 欄不會讓既有查詢意外把圖片拖回來；只有專屬的取圖端點會讀它。

儲存設定沿用 0024 的判斷：內容為已壓縮的 JPEG，TOAST 再壓縮沒有效益且吃 CPU，
故 SET STORAGE EXTERNAL（只外置、不壓縮）。

downgrade：移除主表欄位並還原 patent_attributes."主附圖" TEXT（原欄只存過空白字串，
無資料保留價值，故不做資料回搬）。
"""
from __future__ import annotations

from alembic import op


revision = "0026_patent_main_figure"
down_revision = "0025_report_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE core_layer.patents ADD COLUMN IF NOT EXISTS "主附圖" BYTEA')
    # 已壓縮的 JPEG 不再壓縮，只走 TOAST 外置儲存，取圖與寫入各少一次壓縮／解壓。
    op.execute('ALTER TABLE core_layer.patents ALTER COLUMN "主附圖" SET STORAGE EXTERNAL')
    op.execute(
        'COMMENT ON COLUMN core_layer.patents."主附圖" IS '
        "'WIPS Excel 內嵌的專利代表圖原始位元組（多為 JPEG）；來源為錨在該資料列的浮動圖片"
        "物件，非儲存格文字。無圖為 NULL'"
    )
    # 舊落點只存過空白字串（浮動圖片與儲存格值無關），無保留價值，直接移除避免雙落點。
    op.execute('ALTER TABLE core_layer.patent_attributes DROP COLUMN IF EXISTS "主附圖"')


def downgrade() -> None:
    op.execute('ALTER TABLE core_layer.patents DROP COLUMN IF EXISTS "主附圖"')
    op.execute('ALTER TABLE core_layer.patent_attributes ADD COLUMN IF NOT EXISTS "主附圖" TEXT')
