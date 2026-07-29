"""申請人展開視圖：共同申請人在分析統計中各自計數（2026-07-29 使用者定案）

Revision ID: 0042_applicant_expanded_view
Revises: 0041_drop_legacy_company_name
Create Date: 2026-07-29

## 為什麼

WIPS 以 ` | ` 分隔同一筆專利的多個申請人。使用者定案**三層各自不同**：

| 層 | 處理 | 落點 |
|---|---|---|
| 詳情層顯示 | **保留完整字面** `A \| B` | `report_patent_base."申請人"`（不動） |
| 待補代碼清單 | 拆開 | `api/company_aliases.py`（2026-07-28 已修） |
| **分析統計** | **拆成兩筆各自計數** | **本 migration** |

使用者原話：「這樣顯示可以維持，只要待補專利權人代碼以及後面分析可以分開就可以了」
「拆成兩筆計數」「可以，這是專利分析慣例」。

## 為何用 VIEW 而不改 report_patent_base

`report_patent_base` 是**一專利一列**的寬表，這個語意必須保持——詳情層、
其他 13 個 aggregate 報表、家族表都依賴它。若改成一專利多列，那些報表
全部會重複計數（例如專利總數 60 變成 74）。

故另建展開 VIEW，**只給三個申請人報表使用**：
`applicant_ranking`／`applicant_country_distribution`／`applicant_year_matrix`。

## 件數總和會大於專利總數

共同申請一筆算兩家，這是刻意的（使用者確認為專利分析慣例）。
報表需加註「含共同申請，總和大於專利件數」。

## 為何是 VIEW 不是實體表

`report_patent_base` 本身由 `refresh_derived` 全量重建；VIEW 自動跟著更新，
不必在 refresh 流程加第二個步驟（少一個「忘記同步」的坑）。
效能：60 筆資料無感；上萬筆時再評估物化。

## downgrade

僅 DROP VIEW。報表定義改回原來源由程式碼版本控制，不在 migration 內處理
——那是程式碼不是 schema。
"""
from __future__ import annotations

from alembic import op


revision = "0042_applicant_expanded_view"
down_revision = "0041_drop_legacy_company_name"
branch_labels = None
depends_on = None


VIEW_NAME = "derived_layer.report_patent_applicant_expanded"

# ⚠ 展開的是**原始欄位**（"申請人"），不是收斂後的 applicant_display_name
# ——後者已被 split_part 取成主申請人（2026-07-28 的顯示定案），拿它展開只會
# 得到一筆。原始欄位才保有完整的 `A | B`。
#
# 展開後每個名稱各自走一次收斂：先查對照表（別稱→代碼→顯示名），查不到才用原字面。
# 這與 refresh_report_patent_base 的收斂順位一致，不另立一套。
CREATE_SQL = f"""
CREATE OR REPLACE VIEW {VIEW_NAME} AS
WITH expanded AS (
    SELECT
        b.patent_id,
        b.application_year,
        b.publication_year,
        b.country_code,
        b."Orig. IPC(Main)",
        b."Orig. CPC(Main)",
        b.recent_assignee_display_name,
        BTRIM(part) AS raw_applicant,
        -- 主申請人＝第一個；供「主申請」與「共同申請」的區分
        (BTRIM(part) = BTRIM(split_part(COALESCE(b."申請人", ''), '|', 1))) AS is_primary
    FROM derived_layer.report_patent_base b
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
    e.country_code,
    e."Orig. IPC(Main)",
    e."Orig. CPC(Main)",
    e.recent_assignee_display_name,
    e.is_primary,
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


def upgrade() -> None:
    op.execute(CREATE_SQL)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
