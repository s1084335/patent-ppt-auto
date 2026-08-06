"""申請人展開 VIEW 補欄 ＋ report_patent_base 補專利種類兩欄（2026-08-06 使用者定案）

Revision ID: 0045_expanded_view_columns
Revises: 0044_drop_market_tables
Create Date: 2026-08-06

## 為什麼

**A2（改回全拆分）**：2026-07-31 曾把申請人三報表由展開 VIEW 改回第一順位口徑，
2026-08-05 使用者**再次推翻**——理由是正確性，不是偏好：

    實測「曾晴」在 14 件專利／4 個國家具名為共同申請人，
    報表卻只顯示 2 件／1 國。這是報表在陳述不實資訊。

「總和大於專利件數」是**標示問題不是真相問題**（0042 原文件即載明「共同申請一筆算兩家，
這是刻意的，專利分析慣例，報表需加註」）。故改回展開口徑，並在該頁加註。

**0042 的 VIEW 少了幾個欄位**，接回去就會壞或算不出來，本 migration 一併補齊。

## 補了什麼、為什麼

| 欄位 | 給誰用 | 不補會怎樣 |
|---|---|---|
| `申請人`（原始完整字面） | `applicant_ranking` 的 4 個 aggregate（`count_multivalue`／`count_multivalue_transferred`／`count_singlevalue_transferred`／`string_agg_co_values`） | 🔴 **報表直接壞**——那四個 aggregate 指名讀這一欄 |
| `WIPS同族ID` | 權利強度三維之「部署強度」（家族數） | 算不出家族數 |
| `legal_status` | 法律狀態（用於**篩選與敘述**，非維度） | 敘述寫不出「孟喬 5 件 0% 授權」 |
| `patent_type`／`document_kind` | **A4 設計案標示**與專利種類維度 | 設計案 11 件在簡報上仍無交代 |

⚠ **沒有補 `權利要求的項數`**：2026-08-05 使用者定案權利強度**收斂為三維**
（部署強度／路徑多樣性／跨國布局深度），「權利範圍」該維度**已否決**
（範例 V2 全文 0 命中，只有 GPT md 提）。不補不需要的欄。

## 為何 `patent_type`／`document_kind` 要先加進 report_patent_base

實查：`derived_layer.report_patent_base` **兩欄都沒有**（只有 `core_layer.patents` 有）。

⚠ **它是 VIEW 不是實體表**（`derived_layer.report_patent_base` ＝
`SELECT * FROM legacy_0021.report_patent_base`，0021 建立）。
`SELECT *` 在**建立當下**就展開成固定欄位清單，所以加欄要三步（先例＝0029）：

1. `ALTER TABLE legacy_0021.report_patent_base ADD COLUMN`（真正的實體表）
2. **重建** `derived_layer.report_patent_base` 這個相容 VIEW，它才帶得出新欄
3. 再 `CREATE OR REPLACE` 展開 VIEW（它從 derived 那層讀）

⚠ **順序不能顛倒**：展開 VIEW 依賴 base VIEW；base VIEW 沒重建就先改展開 VIEW，
新欄會找不到而整支 migration 失敗。downgrade 則反序——先拆展開 VIEW 與 base VIEW，
不先拆會被相依性擋住 DROP COLUMN。

⚠ 加欄之後還要 `refresh_report_patent_base.py` 的 base CTE 一併搬運，
否則欄位存在但恆為 NULL（本專案發生過：`Curr. IPC/CPC` 兩欄「實體表早有欄位、
refresh 從未搬過」，導致 derived 恆 NULL、報表只能讀 Orig.）。

## 為何用 CREATE OR REPLACE VIEW

VIEW 不是實體表，`CREATE OR REPLACE` 即可改、無資料搬移；隨 `report_patent_base`
重建自動更新，不必在 refresh 流程加第二個步驟（0042 原始理由，沿用）。

## downgrade

VIEW 還原成 0042 的定義（不 DROP——DROP 會讓引用它的報表在降版後直接 500）；
base 表的兩個新欄 DROP。
"""
from __future__ import annotations

from alembic import op

revision = "0045_expanded_view_columns"
down_revision = "0044_drop_market_tables"
branch_labels = None
depends_on = None

VIEW_NAME = "derived_layer.report_patent_applicant_expanded"
BASE = "derived_layer.report_patent_base"          # 相容 VIEW（展開 VIEW 從這讀）
BASE_TABLE = "legacy_0021.report_patent_base"      # 真正的實體表（ALTER 打這裡）

# base 實體表補的兩欄（A4 設計案標示與專利種類維度用）。
# ⚠ 型別對齊 core_layer.patents：兩者皆為 TEXT。
_BASE_COLUMNS = ('"patent_type" TEXT', '"document_kind" TEXT')

# 相容 VIEW 的重建語句（沿用 0029 的做法）：
# `SELECT *` 於建立當下展開為實體表全部欄位，加欄後必須重跑才會同步。
_RECREATE_BASE_VIEW = (
    f"DROP VIEW IF EXISTS {BASE}",
    f"CREATE VIEW {BASE} AS SELECT * FROM {BASE_TABLE}",
)


def _view_sql(inner_cols: str, outer_cols: str) -> str:
    """展開 VIEW 的完整定義。

    `inner_cols`／`outer_cols` 是本版新增欄位在 CTE 內（別名 `b.`）與外層
    （別名 `e.`）的片段。⚠ 兩份明寫、不用字串替換推導——替換在欄名剛好含
    `b.` 時會靜默改錯，而這種錯只會在跑 migration 當下才炸。

    ⚠ 展開的是**原始欄位** `申請人`，不是 `applicant_display_name`——
    後者已被 split_part 取成主申請人（2026-07-28 顯示定案），拿它展開只會得到一筆。
    這一段沿用 0042，不得簡化。
    """
    return f"""
CREATE OR REPLACE VIEW {VIEW_NAME} AS
WITH expanded AS (
    SELECT
        b.patent_id,
        b.application_year,
        b.publication_year,
        b."授權公告年",
        b.country_code,
        b."Orig. IPC(Main)",
        b."Orig. CPC(Main)",
        b.recent_assignee_display_name,
{inner_cols}        BTRIM(part) AS raw_applicant,
        -- 主申請人＝第一個；供「主申請」與「共同申請」的區分
        (BTRIM(part) = BTRIM(split_part(COALESCE(b."申請人", ''), '|', 1))) AS is_primary
    FROM {BASE} b
    CROSS JOIN LATERAL regexp_split_to_table(
        COALESCE(NULLIF(BTRIM(b."申請人"), ''), b.applicant_display_name, ''),
        '\\s*\\|\\s*'
    ) AS part
    WHERE NULLIF(BTRIM(part), '') IS NOT NULL
)
SELECT
    e.patent_id,
    e.application_year,
    e.publication_year,
    e."授權公告年",
    e.country_code,
    e."Orig. IPC(Main)",
    e."Orig. CPC(Main)",
    e.recent_assignee_display_name,
{outer_cols}    e.is_primary,
    -- 收斂順位同 refresh_report_patent_base：對照表顯示名 > 原字面
    COALESCE(
        NULLIF(BTRIM(ca."公司中文名稱"), ''),
        NULLIF(BTRIM(ca."正規化名稱"), ''),
        e.raw_applicant
    ) AS applicant_display_name
FROM expanded e
LEFT JOIN LATERAL (
    SELECT c."公司中文名稱", c."正規化名稱"
    FROM derived_layer.company_aliases c
    WHERE c.review_status = 'confirmed'
      AND lower(regexp_replace(BTRIM(c."別稱"), '\\s+', ' ', 'g'))
        = lower(regexp_replace(e.raw_applicant, '\\s+', ' ', 'g'))
    ORDER BY c.id
    LIMIT 1
) ca ON true
"""


# 本版新增的欄位（每列都帶該專利的值；展開後同一專利的多列值相同）。
# ⚠ 只列一次，內外層由 `_cols()` 各自組——避免同一份清單抄兩遍後分岔。
_ADDED = ("申請人", "WIPS同族ID", "legal_status", "patent_type", "document_kind")


def _cols(alias: str) -> str:
    """組 SELECT 片段；含中文或大寫的欄名一律加雙引號（PostgreSQL 需要）。"""
    lines = []
    for col in _ADDED:
        quoted = col if col.isascii() and col.islower() else f'"{col}"'
        lines.append(f"        {alias}.{quoted},")
    return "\n".join(lines) + "\n"


_NEW_INNER, _NEW_OUTER = _cols("b"), _cols("e")
_OLD_COLS = ""  # 0042 原版沒有這些欄


def upgrade() -> None:
    # ⓪ 先拆展開 VIEW。
    # 🔴 它 `FROM derived_layer.report_patent_base`，而下面 ② 要 DROP 那個 base VIEW
    # ——不先拆，PostgreSQL 會以「其他物件相依」擋住 DROP，整支 migration 失敗。
    # ⚠ 0029 當年沒有這一步是因為展開 VIEW（0042）那時還不存在；照抄 0029 會炸。
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
    # ① 實體表加欄
    for column_def in _BASE_COLUMNS:
        op.execute(f"ALTER TABLE {BASE_TABLE} ADD COLUMN IF NOT EXISTS {column_def};")
    op.execute(
        f'COMMENT ON COLUMN {BASE_TABLE}."document_kind" IS '
        "'WIPS 文獻種類；S＝外觀設計。⚠ 判定設計案只能用本欄，patent_type 天生只有 P／U 兩值，"
        "設計案全被歸進 P。'"
    )
    # ② 相容 VIEW 重建，才帶得出新欄（不重建則新欄在 derived 層看不到）
    for stmt in _RECREATE_BASE_VIEW:
        op.execute(stmt)
    # ③ 展開 VIEW 補欄（它從 ② 那層讀，故必須排在 ② 之後）
    op.execute(_view_sql(_NEW_INNER, _NEW_OUTER))


def downgrade() -> None:
    # 嚴格反序。⚠ 相依鏈是 展開 VIEW → base VIEW → 實體表，拆除必須由外而內。
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
    op.execute(f"DROP VIEW IF EXISTS {BASE}")
    for column_def in _BASE_COLUMNS:
        name = column_def.split()[0]
        op.execute(f"ALTER TABLE {BASE_TABLE} DROP COLUMN IF EXISTS {name};")
    op.execute(f"CREATE VIEW {BASE} AS SELECT * FROM {BASE_TABLE}")
    # ⚠ 展開 VIEW 還原成 0042 定義而非留著不建——不建的話引用它的報表降版後直接 500。
    op.execute(_view_sql(_OLD_COLS, _OLD_COLS))
