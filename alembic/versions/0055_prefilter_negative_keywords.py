"""初階篩選：負面關鍵字表（PRE-001，2026-08-21）。

## 為什麼要一張新表

初階篩選的**輸入**（使用者給的負面關鍵字與其英文比對詞）與**結果**
（哪些專利被剔除）是兩件事。結果沿用既有的 `workspace_excluded_patents`
（schema 完全不改），輸入才需要這張表。

## 落 derived_layer 的理由

沿 0035 `workspace_excluded_patents` 的先例：它是「衍生自使用者操作、
可重建、綁 workspace」的資料，與 `app_layer` 的核心業務物件分層不同。

## 🔴 `terms_confirmed` 是護欄不是欄位裝飾

`ai:keyword_expand` 寫入時**一律** `false`，只有使用者操作能改 `true`；
未確認的 `match_terms` 不得用於比對、不得產生任何待裁決項目（PRE-002）。
與 `store_ai_verdicts` 只能寫 `pending` 同一個設計。

⚠ 預設值寫在 schema 而不是只靠應用層：應用層漏了一條路徑就破功，
schema 預設是最後一道。

## 欄位取捨

- `match_terms TEXT[]`：一個原始詞對多個英文比對詞（同義詞、詞形）。
  用陣列而非另開一張明細表——它永遠整組讀寫，沒有逐詞查詢的需求。
- `UNIQUE (workspace_id, original_term)`：同一 workspace 內同一個原始詞
  只該有一筆；重複輸入應更新而非新增。
- `enabled`：停用者保留紀錄但不參與比對（PRE-001），故不用刪除代替。

⚠ **不加 `patent_id` 相關欄**：這張表是「規則」不是「結果」，
命中結果一律落 `workspace_excluded_patents`。混在一起會讓「規則改了、
舊結果還在」變成無法表達的狀態。

workspace FK `ON DELETE CASCADE`：workspace 刪掉時規則一併消失，同 0035。

downgrade 直接 DROP：本表無其他物件依賴。
"""
from __future__ import annotations

from alembic import op

revision = "0055_prefilter_negative_keywords"
down_revision = "0054_normalization_asked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE derived_layer.workspace_negative_keywords (
            keyword_id      BIGSERIAL PRIMARY KEY,
            workspace_id    BIGINT NOT NULL
                REFERENCES app_layer.workspaces(workspace_id) ON DELETE CASCADE,
            original_term   TEXT NOT NULL,
            match_terms     TEXT[] NOT NULL DEFAULT '{}',
            terms_confirmed BOOLEAN NOT NULL DEFAULT false,
            enabled         BOOLEAN NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, original_term)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_workspace_negative_keywords_ws "
        "ON derived_layer.workspace_negative_keywords (workspace_id)"
    )
    op.execute(
        "COMMENT ON TABLE derived_layer.workspace_negative_keywords IS "
        "'初階篩選的負面關鍵字（規則，非結果）。命中結果一律落 "
        "workspace_excluded_patents；本表只存使用者輸入的原始詞與其英文比對詞。'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_negative_keywords.terms_confirmed IS "
        "'🔴 護欄：AI 寫入時一律 false，只有使用者操作能改 true。"
        "未確認的 match_terms 不得用於比對、不得產生待裁決項目（PRE-002）。'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_negative_keywords.enabled IS "
        "'停用者保留紀錄但不參與比對（PRE-001）——故不以刪除代替停用。'"
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS derived_layer.workspace_negative_keywords")
