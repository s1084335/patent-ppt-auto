# Proposal: 收斂 workspace 取證範圍為單一機制（unify-workspace-scope）

## Why

2026-08-18 把主線併進 deck 線時發現：**同一件事有兩套實作，讀不同的環境變數**。

| | 環境變數 | 綁定入口 | 誰在用 | 做法 |
|---|---|---|---|---|
| deck 線 | `PATENT_RESEARCH_WORKSPACE_ID` | `workspace_scope_env` | `ai_report_deck_runner`（4 處） | 改寫 SQL：注入 `workspace_scope(patent_id)` CTE ＋ join 閘門 |
| 主線 | `PATENT_REPORT_WORKSPACE_ID` | `narrative_report_scope` | `ai_narrative_runner`（1 處） | 執行後過濾回傳列 |

### P1｜兩者不等價，弱的那套用「規則」補洞

post-filter 只能砍列，**擋不住彙總**：`SELECT count(*) FROM patents` 會用全庫算完，
再對一列結果做過濾——數字是錯的，但畫面看起來完全正常。

主線的補法是寫進 prompt：

> 當報表綁 workspace 時，`query_database` 不得用於彙總（`COUNT`／`SUM`／`GROUP BY`）

⚠ 那是**規則**不是機制：靠 AI 自律，沒有東西擋。而 deck 那套 join 閘門是機制——
沒 join `workspace_scope` 就直接拒絕，且錯誤訊息就是使用說明。

### P2｜偏差型態不同：一個看得見，一個缺席

- 閘門：查詢被拒 → CLI 收到錯誤、知道怎麼改（偏差是「多出來的」）
- post-filter：列被靜默丟掉 → 沒有人會發現少了什麼（**缺席型**）

### P3｜同一份知識兩個定義處

「這個任務綁哪個 workspace」有兩個來源。今天靠「呼叫端不重疊」僥倖沒出事
（deck runner 只設研究變數、narrative runner 只設報表變數），但兩者都在同一個
`query_database` 裡被讀，任何一邊擴大使用範圍就會互相干擾，而且不會報錯。

## What Changes

1. 單一環境變數與單一綁定入口；`narrative_report_scope` 保留 snapshot 綁定，
   workspace 綁定改為委派給統一入口。
2. `query_database` 只剩一條 scope 路徑：注入 `workspace_scope` CTE ＋ join 閘門。
3. 移除 post-filter（`_filter_rows_to_workspace`）與「scoped 不得彙總」限制
   （`validate_scoped_narrative_sql` 的彙總封鎖與 patent_id 強制）。
4. 同步兩份 prompt 文件：把「不准彙總」改為「彙總要 JOIN `workspace_scope`」。

## Capabilities

### Modified Capabilities

- `ai-companion`：綁定 workspace 的取證查詢改由單一機制保證，且彙總可用。

## Scope

- `backend/app/mcp_server/report_research.py`
- `backend/app/worker/ai_narrative_runner.py`（若綁定入口改名）
- `backend/app/worker/prompts/data_access.md`、`report-narrative-flow.md`
- 既有測試：`test_report_research_profile.py`、`test_ai_narrative_runner.py`

## Non-goals

- 不改 deck 線既有的閘門判準（`_PATENT_SCOPED_TABLES` 白名單維持原樣）。
- 不改稽核（`_audit`）與唯讀交易設定。
- 不動 `query_report_evidence` 等快照工具。

## Impact

- Affected specs: `ai-companion`
- Affected behaviour（對綁定 workspace 的 narrative 任務）：
  - **放寬**：彙總從「一律拒絕」變成「JOIN `workspace_scope` 即可」
  - **收緊**：查 patent 級表沒 JOIN `workspace_scope` 會被拒絕（原本是靜默過濾）
  - 回傳結果不再需要含 `patent_id`／`id`
- 無 migration

## Activation

- 後端與 worker 重啟後生效；前端不受影響。
- ⚠ prompt 文件是 CLI 讀的，隨程式一起部署。

## Acceptance Gate

1. 只剩一個 workspace scope 環境變數與一個綁定入口。
2. 綁定後查 patent 級表未 JOIN `workspace_scope` → 被拒，訊息說明怎麼改。
3. 綁定後 `SELECT count(*) … JOIN workspace_scope` → **可執行且結果只涵蓋該 workspace**。
4. 空 workspace（無成員）→ 拒絕，不得靜默退回全庫。
5. 未綁定時行為與原本一般唯讀查詢完全相同。
6. 兩份 prompt 文件不再有「不得彙總」的敘述。
7. 範圍回歸全綠；主線既有的 scope 測試改寫後仍覆蓋原本要守的保證。

## Confirmed Decisions

- 2026-08-18（使用者裁決）：兩套收斂成一套。
- 2026-08-18：保留 **SQL 改寫＋閘門**，捨棄 post-filter。理由是「機制 vs 規則」與
  「可見偏差 vs 缺席偏差」，見 Why。
- 2026-08-18：**修正**既有決策「scoped narrative 不得彙總」
  （`scope-narrative-evidence-to-workspace`，2026-08-18 已 archive）。該限制存在的
  唯一理由是 post-filter 修不了彙總；換成 SQL 改寫後理由消失。原本要守的
  「不得用全庫數字冒充 workspace 數字」由 join 閘門承接，且比原本更強
  （原本只是叫 AI 不要做）。

## Open Questions

無。
