"""專利欄位重分類：會被程式用到的欄位搬進 core table（2026-08-06）

Revision ID: 0046_core_field_reclassification
Revises: 0045_expanded_view_columns
Create Date: 2026-08-06

規格：`docs/patent_core_field_reclassification_spec.md`
Preflight 證據：`D:\\力山\\.agents\\context\\patent-db-claude-plan.md`

## 目標

```text
會被分析／分群／報表／查詢／案件比對用到的欄位 -> core_layer.patents / patent_people
完全沒被使用的 WIPS 欄位                      -> core_layer.patent_attributes
完整原始列                                    -> raw_records.raw_data（不動）
```

## 為什麼要搬

`patent_attributes` 是**一 raw_record 一列**的寬表，同一專利多次匯入就有多列。
runtime 為了取值得寫「取最新非空」的相關子查詢（`patent_queries._attribute_pick`、
`refresh_report_patent_base` 的三個 LATERAL）——每個讀取端各自實作一次「哪一列才算數」，
⚠ **選列規則散在多處**，而且結果不保證一致（`refresh` 用 `pa2` 子查詢、
`patent_queries` 用 `raw_record_id DESC`）。搬進 core table 後一專利一列，
canonical value 只有一個，選列問題消失。

## 搬了什麼（preflight 實查，16 欄全部有值）

| 欄位 | 有值 /55 | 誰在讀 |
|---|---|---|
| 摘要(原文) / 未審查的公開日 / 授權公告日 / 優先權三欄 / 詳細查看連結 | 47/16/44/7/7/7/55 | `patent_queries._ATTRIBUTE_FIELDS` |
| 文圖像文件(PDF)連結 | 55 | `target_source`（案件比對）＋ `patent_queries` |
| WIPS同族各國家文獻數量 / EPC 兩欄 / (F1)(B1) 引用數 | 55/1/1/55/55 | `refresh_report_patent_base` |
| 解決課題 摘要 | 44 | 功效通道 AI 補分輸入（2026-08-05 定案） |
| 發明人數 / 申請人數 | 55/55 | `refresh_report_patent_base`（發明人數）；申請人數屬 people 統計 |

## 回填規則

⚠ **取每件專利的最新非空值**（`raw_record_id DESC`），空值不得覆蓋非空。

🔴 **成對欄位必須取自同一 raw_record**：`EPC有效國家[EP]` 與 `EPC無效國家[EP]`
若各自取「最新非空」，可能落在不同匯入批次——一個說有效國是 A、另一個說無效國是
B 批次的值，語意直接矛盾。故以 `PAIRED_GROUPS` 宣告，同組欄位一次從同一列取。

## 不搬什麼（規格明列，本次不得順手加）

- **All classification**（`Orig./Curr. IPC/CPC(All)`）：多值分類碼，整串放進 patents
  做 group by 會統計失真。要分析請另案設計 `patent_classifications` 展開表。
- **其他 AI 摘要欄**（AI摘要／技術領域／解決手段／特徵）：目前無程式使用。
- ⚠ 兩者都有測試擋著（`tests/test_core_field_reclassification.py`），避免日後被順手搬走。

## downgrade

把欄位加回 `patent_attributes` 並從 core table 回填。
⚠ 回填只能填到「該專利的每一列」——原本各 raw_record 的差異已無法還原
（canonical value 只有一個）。完整歷史仍在 `raw_records.raw_data`，未受影響。
"""
from __future__ import annotations

from alembic import op

revision = "0046_core_field_reclassification"
down_revision = "0045_expanded_view_columns"
branch_labels = None
depends_on = None

PATENTS = "core_layer.patents"
PEOPLE = "core_layer.patent_people"
ATTRS = "core_layer.patent_attributes"

# 搬到 patents 的欄位（DB 欄名＝繁體；WIPS 原始簡體名在 mappings/wips.py）。
# ⚠ 型別一律 TEXT——與 patent_attributes 原本一致；日期字串的解析留給讀取端，
# 本次只搬家不改型別（改型別會讓回填要處理格式，兩件事混在一起難驗）。
PATENT_MOVES = (
    "摘要(原文)",
    "未審查的公開日",
    "授權公告日",
    "優先權號",
    "優先權國家",
    "優先權日",
    "詳細查看連結(登入)",
    "文圖像文件(PDF)連結",
    "WIPS同族各國家文獻數量(申請為準)",
    "EPC有效國家[EP]",
    "EPC無效國家[EP]",
    "(F1)引用文獻數",
    "(B1)引用文獻數",
    "解決課題 摘要[US,EP,PCT,JP,KR,CN,TW]",
)

PEOPLE_MOVES = ("發明人數", "申請人數")

# 🔴 成對欄位：必須取自**同一** raw_record，否則兩欄語意不一致。
PAIRED_GROUPS = (
    ("EPC有效國家[EP]", "EPC無效國家[EP]"),
)


def _single_backfill(target_table: str, column: str) -> str:
    """單欄回填：取該專利最新一列**非空**值。

    ⚠ `ORDER BY raw_record_id DESC` 是「最新」的定義；`NULLIF(BTRIM(...),'')`
    確保空字串與全空白都算沒有值——WIPS 空欄填的是 ' ' 不是 NULL（既有踩坑）。
    """
    return f"""
        UPDATE {target_table} t
        SET "{column}" = sub.val
        FROM (
            SELECT DISTINCT ON (a.patent_id) a.patent_id,
                   NULLIF(BTRIM(a."{column}"::text), '') AS val
            FROM {ATTRS} a
            WHERE NULLIF(BTRIM(a."{column}"::text), '') IS NOT NULL
            ORDER BY a.patent_id, a.raw_record_id DESC
        ) sub
        WHERE t.patent_id = sub.patent_id
    """


def _paired_backfill(columns: tuple[str, ...]) -> str:
    """成對回填：同一 raw_record 一次取整組，避免兩欄來自不同匯入批次。

    ⚠ 選列條件是「**任一**欄非空的最新列」——不是「每欄各自最新非空」。
    後者會讓有效國取 A 批、無效國取 B 批，兩欄拼起來語意矛盾。
    """
    cols = ", ".join(f'"{c}" = sub."{c}"' for c in columns)
    picks = ", ".join(f'NULLIF(BTRIM(a."{c}"::text), \'\') AS "{c}"' for c in columns)
    any_nonblank = " OR ".join(
        f"NULLIF(BTRIM(a.\"{c}\"::text), '') IS NOT NULL" for c in columns)
    return f"""
        UPDATE {PATENTS} t
        SET {cols}
        FROM (
            SELECT DISTINCT ON (a.patent_id) a.patent_id, {picks}
            FROM {ATTRS} a
            WHERE {any_nonblank}
            ORDER BY a.patent_id, a.raw_record_id DESC
        ) sub
        WHERE t.id = sub.patent_id
    """


def upgrade() -> None:
    paired_flat = {c for group in PAIRED_GROUPS for c in group}

    # ① core table 加欄
    for col in PATENT_MOVES:
        op.execute(f'ALTER TABLE {PATENTS} ADD COLUMN IF NOT EXISTS "{col}" TEXT')
    for col in PEOPLE_MOVES:
        op.execute(f'ALTER TABLE {PEOPLE} ADD COLUMN IF NOT EXISTS "{col}" TEXT')

    # ② 從 attributes 回填（成對的走 paired，其餘走 single）
    for group in PAIRED_GROUPS:
        op.execute(_paired_backfill(group))
    for col in PATENT_MOVES:
        if col in paired_flat:
            continue
        # patents 的 PK 是 id 不是 patent_id，單獨處理
        op.execute(_single_backfill(PATENTS, col).replace(
            "WHERE t.patent_id = sub.patent_id", "WHERE t.id = sub.patent_id"))
    for col in PEOPLE_MOVES:
        op.execute(_single_backfill(PEOPLE, col))

    # ③ 程式都改讀 core table 後，才從 attributes 移除
    for col in PATENT_MOVES + PEOPLE_MOVES:
        op.execute(f'ALTER TABLE {ATTRS} DROP COLUMN IF EXISTS "{col}"')


def downgrade() -> None:
    # ① 欄位加回 attributes
    for col in PATENT_MOVES + PEOPLE_MOVES:
        op.execute(f'ALTER TABLE {ATTRS} ADD COLUMN IF NOT EXISTS "{col}" TEXT')

    # ② 從 core table 回填到該專利的每一列
    # ⚠ 原本各 raw_record 的差異已無法還原（canonical value 只有一個）；
    #    完整歷史仍在 raw_records.raw_data，未受影響。
    for col in PATENT_MOVES:
        op.execute(f"""
            UPDATE {ATTRS} a SET "{col}" = p."{col}"
            FROM {PATENTS} p WHERE a.patent_id = p.id
        """)
    for col in PEOPLE_MOVES:
        op.execute(f"""
            UPDATE {ATTRS} a SET "{col}" = pp."{col}"
            FROM {PEOPLE} pp WHERE a.patent_id = pp.patent_id
        """)

    # ③ 移除 core table 的欄位
    for col in PATENT_MOVES:
        op.execute(f'ALTER TABLE {PATENTS} DROP COLUMN IF EXISTS "{col}"')
    for col in PEOPLE_MOVES:
        op.execute(f'ALTER TABLE {PEOPLE} DROP COLUMN IF EXISTS "{col}"')
