"""家族國別佈局加「狀態未知／審查中」兩欄（2026-07-28 使用者定案）。

## 為什麼

國家佈局圖完全看不到 TW。查清結果**不是 bug，是資料來源沒有 TW 狀態**：
WIPS 匯出的狀態欄名為 `状态[US,JP,KR,CN,EP,CA,AU]`，欄名本身就列明涵蓋範圍不含 TW。
實測 CN 39／US 9／EP 3 全部有值，TW 9 筆全空 → `normalize_legal_status` 一律回
unknown → `build_family_country_dataset` 原本對 unknown／pending 直接 `continue`，
那 9 筆（同族 ID 100% 齊全、其中 6 筆是自家 M 開頭新型）在佈局圖上完全消失。

使用者定案：**有同族 ID 的都要能納入分析，不分國家**。

## 為什麼不直接併進 direct_patent_count

`family_country_layout` 報表的定義是**現有保護國家佈局**（按家族去重、只算存活）。
把狀態不明的件併進 direct 等於宣稱「它確定還有保護」——那是捏造。
故另立兩欄分開計數，呈現層可顯示「CN 25 家族（另 2 家族狀態未知）」這種誠實資訊。

dead（到期／失效）維持排除——那是明確的「已無保護」，與「不知道」不同。

## 為什麼動 legacy_0021 而非 derived_layer

0021 之後 `derived_layer.report_family_country` 是相容 VIEW，實體表在
`legacy_0021`。對 VIEW 加欄會失敗；且 VIEW 需一併重建，否則報表端讀不到新欄。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0038_family_status_counts"
down_revision = "0037_exclusion_restore_topic"
branch_labels = None
depends_on = None


# VIEW 定義（照抄現行 pg_get_viewdef 再加兩欄，順序與實體表一致）
_VIEW_WITH_NEW = """
CREATE OR REPLACE VIEW derived_layer.report_family_country AS
SELECT family_id,
       country_code,
       direct_patent_count,
       via_ep_count,
       unknown_status_count,
       pending_status_count,
       family_incomplete,
       is_surrogate_family
  FROM legacy_0021.report_family_country;
"""

_VIEW_ORIGINAL = """
CREATE OR REPLACE VIEW derived_layer.report_family_country AS
SELECT family_id,
       country_code,
       direct_patent_count,
       via_ep_count,
       family_incomplete,
       is_surrogate_family
  FROM legacy_0021.report_family_country;
"""


def upgrade() -> None:
    # server_default='0'：既有 32 列不能違反 NOT NULL；新列由寫入端明確給值。
    for col in ("unknown_status_count", "pending_status_count"):
        op.add_column(
            "report_family_country",
            sa.Column(col, sa.Integer(), nullable=False, server_default="0"),
            schema="legacy_0021",
        )
    # 欄位順序變更需重建 VIEW（CREATE OR REPLACE 不允許改既有欄順序時，先 DROP）。
    op.execute("DROP VIEW IF EXISTS derived_layer.report_family_country;")
    op.execute(_VIEW_WITH_NEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS derived_layer.report_family_country;")
    for col in ("pending_status_count", "unknown_status_count"):
        op.drop_column("report_family_country", col, schema="legacy_0021")
    op.execute(_VIEW_ORIGINAL)
