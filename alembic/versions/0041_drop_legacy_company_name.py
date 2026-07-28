"""移除 company_aliases 舊欄 `公司名稱`，全表收斂四欄（2026-07-28 使用者定案）

Revision ID: 0041_drop_legacy_company_name
Revises: 0040_company_name_split
Create Date: 2026-07-28

## 為什麼

使用者定的欄位清單只有四欄：

    申請人代碼、公司中文名稱、正規化名稱、別稱

使用者原話：「沒有公司名稱，我沒有排這一欄吧?」「這輪一起移除」。

0040 拆四欄時保留 `公司名稱` 供對照，但保留後它同時是：
- **寫入端**：`apply_confirmed_display_names` 仍同步寫入（中文優先）
- **讀取端 fallback**：5 處 COALESCE 的最後一段

＝同一語意兩個落點。本專案 2026-07-27～28 已累計 18 次此類斷鏈，全部是靜默失敗
（空值、空清單、永遠不觸發，不拋錯不進 log）。留著一個「不再寫入但還讀得到」的欄位，
就是下一次落點混淆的種子——故整條移除，不留過渡期。

## 顯示順位（使用者定）

「優先順序是顯示中文、沒中文才正規化，沒正規化才原值」

⚠ 三段落在**兩層**，不是同一個 COALESCE：

| 段 | 來源 | 位置 |
|---|---|---|
| 中文名 | `company_aliases.公司中文名稱` | 對照表層（`code_alias_names` CTE） |
| 正規化 | `company_aliases.正規化名稱` | 同上 |
| **原值** | 專利本身的 `標準化申請人`／`申請人` 等 | **外層** `applicant_display_name` 的 COALESCE 末端 |

原值不能放進對照表層：對照表兩欄皆空的列本就不該參與收斂（WHERE 已濾掉），
在該層補原值會讓「沒填名稱的空組」也搶到 `mode()` 名額。

## 本 migration 做兩件事

1. `DROP COLUMN 公司名稱`。
2. 一併確認舊 UNIQUE 已不存在（0040 已 drop，此處 IF EXISTS 冪等保底）——
   該約束的定義含 `公司名稱`，欄位不在時無法重建，downgrade 需自行處理。

## 資料影響

DROP 前**不搬資料**：拆欄後 `公司名稱` 的值都是由新兩欄推導出來的複本
（`apply_confirmed_display_names` 寫的是 `zh_name or en_name`），沒有獨有資訊。
拆欄前寫入的既有列（唯一一組 TW-CHIHUA 4 列）由使用者從前端重走一次流程歸位，
使用者已明示「清掉，我從前端歸一次」。

## downgrade

加回欄位 → 以新兩欄回填（中文優先）→ 還原 NOT NULL 與舊 UNIQUE。
⚠ 兩個新欄皆空的列回填後仍是 NULL，`SET NOT NULL` 會失敗——這是預期行為，
不靜默刪列或塞假值。真要 downgrade 需先人工補齊那些列。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0041_drop_legacy_company_name"
down_revision = "0040_company_name_split"
branch_labels = None
depends_on = None


LEGACY_COLUMN = "公司名稱"
LEGACY_UNIQUE = "company_aliases_申請人代碼_公司名稱_別稱_key"


def upgrade() -> None:
    # 0040 已 drop，這裡是冪等保底：約束定義含欄位名，欄位先 drop 會失敗。
    op.execute(
        "ALTER TABLE derived_layer.company_aliases "
        f'DROP CONSTRAINT IF EXISTS "{LEGACY_UNIQUE}";'
    )
    op.drop_column("company_aliases", LEGACY_COLUMN, schema="derived_layer")


def downgrade() -> None:
    op.add_column(
        "company_aliases",
        sa.Column(LEGACY_COLUMN, sa.Text(), nullable=True),
        schema="derived_layer",
    )
    # 以新兩欄回填（中文優先，與拆欄前 apply_confirmed_display_names 的寫法一致）。
    op.execute(
        "UPDATE derived_layer.company_aliases "
        f'SET "{LEGACY_COLUMN}" = COALESCE("公司中文名稱", "正規化名稱");'
    )
    op.execute(
        "ALTER TABLE derived_layer.company_aliases "
        f'ADD CONSTRAINT "{LEGACY_UNIQUE}" UNIQUE ("申請人代碼", "{LEGACY_COLUMN}", "別稱");'
    )
    op.execute(
        "ALTER TABLE derived_layer.company_aliases "
        f'ALTER COLUMN "{LEGACY_COLUMN}" SET NOT NULL;'
    )
