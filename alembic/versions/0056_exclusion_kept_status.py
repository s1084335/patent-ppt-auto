"""排除清單加 'kept' 狀態與 'prefilter' 來源（CLU-017，2026-08-21）。

## 為什麼推翻 0036 的「保留＝刪列」

0036 明確寫過不留第三種狀態，理由是「另立狀態會讓每個查排除清單的地方都要多一個
過濾條件」。**這個理由在當時成立，但需求變了**：

`CLU-017` 要求「已裁決保留者，任一條線再次執行都不得再列為待裁決」。
刪列＝**記不住誰被保留過**，每次重跑初階篩選或 AI 判讀都會把同一批專利
重新列出來要使用者再裁決一次——那不是可接受的成本，是流程本身不能用。

使用者 2026-08-21 裁決：「那就把既有契約修一下」。

## ⚠ 0036 擔心的事實際上不成立（動工前窮舉查證）

窮舉全庫所有碰 `workspace_excluded_patents` 的查詢（`exclusions.py` 10 處、
`clustering/runner.py` 1 處），**每一個都明確指定 status**：

| 查詢 | 條件 | 加 kept 後 |
|---|---|---|
| `excluded_patent_ids` | `status='excluded'` | 不受影響 |
| `analysis_member_patent_ids` | `status='excluded'` | 不受影響 |
| `restore_patents` | `status='excluded'` | 不受影響 |
| `pending_reviews` | `status='pending'` | 不受影響 |
| `excluded_patent_rows` | `status='excluded'` | 不受影響 |
| `confirm_exclusions` | `pending`→`excluded` | 不受影響 |
| `store_ai_verdicts` ON CONFLICT | `WHERE status='pending'` | **正好**：不覆蓋已保留者 |
| `clustering/runner.py` | `status='excluded'` | 不受影響 |

⇒ 沒有任何「不帶 status 條件全取」的查詢，所以已保留者不會混進任何既有清單。
⚠ 這個性質必須**用測試守住**（見 `test_exclusion_kept_status.py` 的結構檢查），
否則日後新增一個不帶條件的查詢就會破功——那才是 0036 真正擔心的事。

## source 加 'prefilter'

初階篩選是第三條產生排除的線（原有 manual／ai）。分開標記才能回答
「這件是被哪條線抓到的」，也讓 PRE-005 的命中原因可追溯。
"""
from __future__ import annotations

from alembic import op

revision = "0056_exclusion_kept_status"
down_revision = "0055_prefilter_negative_keywords"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP CONSTRAINT IF EXISTS workspace_excluded_patents_status_check"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "ADD CONSTRAINT workspace_excluded_patents_status_check "
        "CHECK (status IN ('pending', 'excluded', 'kept'))"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP CONSTRAINT IF EXISTS workspace_excluded_patents_source_check"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "ADD CONSTRAINT workspace_excluded_patents_source_check "
        "CHECK (source IN ('manual', 'ai', 'prefilter'))"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.status IS "
        "'pending（待人工裁決）｜excluded（已確定排除）｜kept（使用者裁決保留）。"
        "🔴 只有 excluded 會被分析成員扣除；pending 與 kept 都不影響分析。"
        "kept 於 2026-08-21 加入（CLU-017）：保留必須記得住，否則每次重跑都會"
        "把同一批專利重新列為待裁決。'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.source IS "
        "'manual（人工剔除）｜ai（AI 判讀）｜prefilter（初階篩選負面關鍵字）。"
        "分開標記才能回答「這件是被哪條線抓到的」。'"
    )


def downgrade() -> None:
    """⚠ 回復前必須先處理既有的 kept／prefilter 列，否則 CHECK 會加不上去。

    保留列還原成「不在清單上」＝刪列（回到 0036 的語意）；
    prefilter 來源的列改標 manual（它們確實是使用者裁決的結果）。
    """
    op.execute(
        "DELETE FROM derived_layer.workspace_excluded_patents WHERE status = 'kept'")
    op.execute(
        "UPDATE derived_layer.workspace_excluded_patents "
        "SET source = 'manual' WHERE source = 'prefilter'")
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP CONSTRAINT IF EXISTS workspace_excluded_patents_status_check"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "ADD CONSTRAINT workspace_excluded_patents_status_check "
        "CHECK (status IN ('pending', 'excluded'))"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP CONSTRAINT IF EXISTS workspace_excluded_patents_source_check"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "ADD CONSTRAINT workspace_excluded_patents_source_check "
        "CHECK (source IN ('manual', 'ai'))"
    )
