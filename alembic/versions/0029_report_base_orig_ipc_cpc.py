"""legacy_0021.report_patent_base 補 "Orig. IPC(Main)"／"Orig. CPC(Main)" 兩欄並重建相容 VIEW。

Revision ID: 0029_report_base_orig_ipc_cpc
Revises: 0028_global_workspace
Create Date: 2026-07-23

背景：報表分析的 IPC／CPC 來源已從 `Curr. ...(Main)`（WIPS 匯出的**現行**分類，會隨再分類
變動）改為 `Orig. ...(Main)`（**原始**分類，對「當年申請時的技術定位」才是正確口徑），
`backend/app/derived/refresh_report_patent_base.py` 的 SELECT 與 INSERT 欄位清單也已改成
Orig.。但衍生表本身從未加過這兩欄——0021 建表時只有 Curr. 兩欄，來源端 Orig. 只存在於
core_layer.patents。結果不是「報表出空值」，而是 refresh 與報表 query **直接報錯**：

    SELECT "Orig. IPC(Main)" FROM derived_layer.report_patent_base
    → column "Orig. IPC(Main)" does not exist

本版補上欄位，讓已改好的 refresh 能寫入、報表能讀出。

**為何必須連 VIEW 一起 DROP/CREATE**：derived_layer.report_patent_base 是 0021 建的相容
VIEW，雖然當初寫成 `SELECT *`，PostgreSQL 在 CREATE VIEW 當下就把 `*` 展開成當時的欄位
清單並固化（pg_get_viewdef 可見展開後的逐欄列表）。之後對底層實體表 ADD COLUMN，VIEW
**不會**自動帶出新欄——這正是本次缺陷「只加欄位仍然報錯」的真正成因。故必須重建 VIEW。

重建同樣用 `SELECT *`：新的展開結果即為實體表當下的**全部欄位、原 ordinal_position 順序**，
既有欄位（含 Curr. 兩欄與 0021 之後各版陸續加的欄）機械性完整保留，不需人工抄欄位清單，
也就沒有抄漏或改序的風險。

只增不減：`Curr. IPC(Main)`／`Curr. CPC(Main)` 一律保留，避免影響其他仍讀 Curr. 的讀取者。

型別取 TEXT，與來源 core_layer.patents 的同名欄位一致。

downgrade：反向 DROP 兩欄並以同樣方式重建 VIEW（回到不含 Orig. 兩欄的投影）。
"""
from __future__ import annotations

from alembic import op


revision = "0029_report_base_orig_ipc_cpc"
down_revision = "0028_global_workspace"
branch_labels = None
depends_on = None


# 相容 VIEW 的重建語句：`SELECT *` 於建立當下展開為實體表全部欄位，
# 故加欄／減欄後都要重跑一次，VIEW 才會與實體表同步。
_RECREATE_VIEW = (
    "DROP VIEW IF EXISTS derived_layer.report_patent_base",
    "CREATE VIEW derived_layer.report_patent_base AS "
    "SELECT * FROM legacy_0021.report_patent_base",
)


def upgrade() -> None:
    op.execute('ALTER TABLE legacy_0021.report_patent_base ADD COLUMN IF NOT EXISTS "Orig. IPC(Main)" TEXT')
    op.execute('ALTER TABLE legacy_0021.report_patent_base ADD COLUMN IF NOT EXISTS "Orig. CPC(Main)" TEXT')
    op.execute(
        'COMMENT ON COLUMN legacy_0021.report_patent_base."Orig. IPC(Main)" IS '
        "'原始 IPC 主分類（WIPS Orig. IPC(Main)）；報表技術分布以此為準，不用會隨再分類變動的 Curr.'"
    )
    op.execute(
        'COMMENT ON COLUMN legacy_0021.report_patent_base."Orig. CPC(Main)" IS '
        "'原始 CPC 主分類（WIPS Orig. CPC(Main)）；報表技術分布以此為準，不用會隨再分類變動的 Curr.'"
    )
    # 加欄後 VIEW 仍停留在建立時展開的舊欄位清單，必須重建才帶得出新欄。
    for stmt in _RECREATE_VIEW:
        op.execute(stmt)


def downgrade() -> None:
    # 必須**先**移除 VIEW：upgrade 後的 VIEW 已展開並參照 Orig. 兩欄，
    # 不先拆掉就會被相依性擋住而 DROP COLUMN 失敗（不用 CASCADE，避免誤刪其他相依物件）。
    op.execute("DROP VIEW IF EXISTS derived_layer.report_patent_base")
    op.execute('ALTER TABLE legacy_0021.report_patent_base DROP COLUMN IF EXISTS "Orig. IPC(Main)"')
    op.execute('ALTER TABLE legacy_0021.report_patent_base DROP COLUMN IF EXISTS "Orig. CPC(Main)"')
    op.execute(
        "CREATE VIEW derived_layer.report_patent_base AS "
        "SELECT * FROM legacy_0021.report_patent_base"
    )
