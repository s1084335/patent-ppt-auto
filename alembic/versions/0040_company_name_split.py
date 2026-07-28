"""company_aliases 公司名四欄拆分（2026-07-28 使用者定案）

Revision ID: 0040_company_name_split
Revises: 0039_report_base_abstract
Create Date: 2026-07-28

## 為什麼

現況只有**一個** `公司名稱` 欄，混用兩種語意：AI `translated` 寫中文、
`keep_original` 寫英文原文、使用者手動建組填的也是這一欄。
使用者定案：「應該是 申請人代碼 / 公司中文名稱 / 正規化名稱 / 別稱，要這樣分」
——中文正式名與英文正式名是兩種東西，不該擠在同一欄。

目標結構（一組 = 代碼 ＋ 中文名 ＋ 英文正式名 ＋ N 個別稱，每個別稱一列）：

| 欄 | 語意 | 可空 |
|---|---|---|
| 申請人代碼   | WIPS 查來的歸戶依據（無代碼者掛 TEMP: 臨時代碼）| 是 |
| 公司中文名稱 | 中文正式名 | **是** |
| 正規化名稱   | 英文正式名 | **是** |
| 別稱         | 各種雜亂寫法（一列一個）| 否 |

沿用既有表，**不新增 table**（使用者明示）。

## 本 migration 做四件事

1. 加 `公司中文名稱`、`正規化名稱` 兩欄，**皆 nullable**
   （使用者第②點：兩欄都可空、不加 CHECK 強制至少一欄有值——可能先建組後補名）。
2. **不搬資料**（使用者第③點）。自動判斷「含 CJK 就是中文名」對混合字串
   （`XIAMEN DMASTER ... | Zeng Qing`）會判錯且無人覆核。現有唯一一組
   （TW-CHIHUA / 喬山健康科技，4 列）由使用者從前端重走一次流程歸位。
   舊 `公司名稱` 欄保留一段時間供對照，確認無誤後另案移除。
3. 放寬舊 `公司名稱` 的 **NOT NULL**：拆欄後「只填中文名」或「只填英文名」的
   寫入不再填舊欄，留著 NOT NULL 會直接 NotNullViolation。
4. 放寬舊 UNIQUE `(申請人代碼, 公司名稱, 別稱)`：`公司名稱` 淡出後該約束無意義
   （兩列同代碼同別稱但舊欄一空一有值就都放行）。唯一性交給既有 partial unique
   index `ux_company_aliases_code_lookup_confirmed (申請人代碼, alias_lookup_key)
   WHERE review_status='confirmed'`。

   ⚠ **放寬前已確認寫入路徑**：全 repo grep `ON CONFLICT ("申請人代碼", "公司名稱",
   "別稱")` 只有 `govern_company_names` 一處依賴此約束擋重複，本輪同步改為
   `ON CONFLICT ("申請人代碼", alias_lookup_key) WHERE review_status='confirmed'`
   ——與 `import_company_aliases`／`apply_confirmed_display_names` 同一把 key。

## downgrade

對稱還原：移兩欄、還原 NOT NULL、還原舊 UNIQUE。
⚠ 還原 NOT NULL 前要先把空值補上；本 migration 以舊欄既有值為準，
空值填 `公司中文名稱`／`正規化名稱`（拆欄後寫入的列，舊欄本來就空）。
沒有可用值的列（兩欄皆空）downgrade 會失敗——這是預期行為，
不應靜默刪列或塞假值來讓 downgrade 通過。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0040_company_name_split"
down_revision = "0039_report_base_abstract"
branch_labels = None
depends_on = None


LEGACY_UNIQUE = "company_aliases_申請人代碼_公司名稱_別稱_key"


def upgrade() -> None:
    op.add_column(
        "company_aliases",
        sa.Column("公司中文名稱", sa.Text(), nullable=True),
        schema="derived_layer",
    )
    op.add_column(
        "company_aliases",
        sa.Column("正規化名稱", sa.Text(), nullable=True),
        schema="derived_layer",
    )
    op.execute(
        'ALTER TABLE derived_layer.company_aliases '
        'ALTER COLUMN "公司名稱" DROP NOT NULL;'
    )
    op.execute(
        "ALTER TABLE derived_layer.company_aliases "
        f'DROP CONSTRAINT IF EXISTS "{LEGACY_UNIQUE}";'
    )


def downgrade() -> None:
    # 還原 NOT NULL 前先把舊欄補齊：拆欄後寫入的列舊欄是空的，
    # 以新欄的值回填（中文名優先，其次英文正式名）。
    op.execute(
        'UPDATE derived_layer.company_aliases '
        'SET "公司名稱" = COALESCE("公司中文名稱", "正規化名稱") '
        'WHERE NULLIF(BTRIM(COALESCE("公司名稱", \'\')), \'\') IS NULL;'
    )
    op.execute(
        "ALTER TABLE derived_layer.company_aliases "
        f'ADD CONSTRAINT "{LEGACY_UNIQUE}" UNIQUE ("申請人代碼", "公司名稱", "別稱");'
    )
    op.execute(
        'ALTER TABLE derived_layer.company_aliases '
        'ALTER COLUMN "公司名稱" SET NOT NULL;'
    )
    op.drop_column("company_aliases", "正規化名稱", schema="derived_layer")
    op.drop_column("company_aliases", "公司中文名稱", schema="derived_layer")
