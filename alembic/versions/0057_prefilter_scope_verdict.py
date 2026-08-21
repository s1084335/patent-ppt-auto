"""初階篩選的範圍相關性建議（PRE-008，2026-08-21）。

## 為什麼不重用既有的 `ai_verdict`

`ai_verdict`（0036）是 **AI 線**（`ai:irrelevant_filter`）的判定，值域為繁體中文
三分「相干／可疑／不相干」，判準是「**這一筆在它所屬主題裡最不像**」。

PRE-008 判的是「**這一筆與整批專利的技術範圍有沒有關係**」——判準不同、值域不同、
判讀依據也不同（前者要分群結果，後者禁止依賴分群，只讀標題／摘要／獨立項）。

⚠ 判斷是不是同一份知識，看的是**改了 A 是否必須同步改 B**。
改初階篩選的建議詞彙，不必動 AI 線的三分值；反之亦然 ⇒ 不是同一份知識 ⇒ 分欄。
硬塞同一欄的代價是每個讀取端都得先 `if source == ...` 分支，
而那種分支漏一處不會報錯——只會顯示錯的東西。

欄名取 `scope_*` 而非 `ai_*`：命名點出**判的是什麼**（與整批範圍的關係），
和 `ai_verdict`（在主題裡的相干性）自然分得開，不必靠註解記住差異。

## 三種值都要能表達，`NULL` 是第四種

| 值 | 意思 |
|---|---|
| `NULL` | **尚未產生建議**（沒跑過、或跑失敗） |
| `keep` | 建議保留 |
| `exclude` | 建議剔除 |
| `no_basis` | 標題、摘要、獨立項皆空——**無判讀依據** |

🔴 `no_basis` 必須是實體值，不能用 `NULL` 代表。PRE-008 明訂
「SHALL 明確標示該筆尚無建議，SHALL NOT 以空白混充為建議保留」——
「還沒跑」與「跑了但沒有依據」是兩件事，混在一起的話使用者會把後者
當成前者而一直等。⚠ 這是缺席型偏差：看不到的東西不會引起懷疑。

## 為什麼命中文本不存進來

命中的那段原文（使用者 2026-08-21：「命中原因改顯示文本」）由
`prefilter.matching.match_snippets` 於查詢時即算，**不落庫**：

1. 它是**推導得出**的——存下來就是專利內文的第二份副本，會與來源漂移。
2. 180 件 × 86 字的重複文字，換不到任何查詢效益。

⚠ 這與「AI 建議必須落庫」不衝突：建議是 AI 的一次性產物，重算要花錢也不保證
相同；文本是確定性抽取，重算免費且必定相同。**能推導的不存，不能重現的才存。**
"""
from __future__ import annotations

from alembic import op

revision = "0057_prefilter_scope_verdict"
down_revision = "0056_exclusion_kept_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "ADD COLUMN IF NOT EXISTS scope_verdict   TEXT, "
        "ADD COLUMN IF NOT EXISTS scope_reason    TEXT, "
        "ADD COLUMN IF NOT EXISTS scope_judged_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP CONSTRAINT IF EXISTS workspace_excluded_patents_scope_verdict_check"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "ADD CONSTRAINT workspace_excluded_patents_scope_verdict_check "
        "CHECK (scope_verdict IS NULL "
        "       OR scope_verdict IN ('keep', 'exclude', 'no_basis'))"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.scope_verdict IS "
        "'AI 對「與整批專利範圍的關係」的建議：keep｜exclude｜no_basis（三個判讀"
        "欄位皆空）。NULL＝尚未產生建議。🔴 僅為建議，不得改變 status，"
        "正式裁決一律由使用者為之（PRE-008）。"
        "⚠ 與 ai_verdict 不同：那是 AI 線在主題內的相干性判定，判準與值域皆異。'"
    )
    op.execute(
        "COMMENT ON COLUMN derived_layer.workspace_excluded_patents.scope_reason IS "
        "'scope_verdict 的理由。⚠ 與 reason 分欄：reason 是「為什麼被列入這張表」"
        "（初階篩選＝命中的關鍵字），scope_reason 是「為什麼建議留或剔」。"
        "使用者要分得出這兩件事（PRE-008）。'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP CONSTRAINT IF EXISTS workspace_excluded_patents_scope_verdict_check"
    )
    op.execute(
        "ALTER TABLE derived_layer.workspace_excluded_patents "
        "DROP COLUMN IF EXISTS scope_verdict, "
        "DROP COLUMN IF EXISTS scope_reason, "
        "DROP COLUMN IF EXISTS scope_judged_at"
    )
