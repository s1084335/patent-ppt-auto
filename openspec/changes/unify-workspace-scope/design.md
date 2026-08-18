# Design: 收斂 workspace 取證範圍

## 1. 保留哪一套，為什麼

| 判準 | post-filter（主線） | SQL 改寫＋閘門（deck） |
|---|---|---|
| 彙總 | 修不了 → 只能在 prompt 寫「不准做」 | JOIN 了就正確；沒 JOIN 直接拒絕 |
| 偏差型態 | 靜默丟列（缺席型，沒人發現） | 查詢被拒（可見，且訊息就是使用說明） |
| 是規則還是機制 | 規則（靠 AI 自律） | 機制（擋在執行前） |

保留 deck 那套。⚠ 這不是「deck 比較新」，而是**弱的那套需要一條規則來補洞**——
規則補得住的洞，機制本來就不該留。

## 2. 收斂後的形狀

```
runner（deck／narrative）
   └─ workspace_scope_env(workspace_id)        ← 唯一綁定入口，唯一環境變數
         └─ query_database
               ├─ _scope_workspace_id()        ← 唯一讀取點
               ├─ _fetch_workspace_patent_ids  ← 同一筆唯讀交易內取
               └─ _apply_workspace_scope       ← 注入 CTE ＋ join 閘門
```

`narrative_report_scope` **不刪**：它還負責 snapshot 綁定（`PATENT_REPORT_SNAPSHOT_ID`），
那與 workspace 無關。它的 workspace 部分改為委派給 `workspace_scope_env`。

⚠ 委派而不是「兩邊都設」——兩邊都設就等於保留兩個定義處，只是換個地方漂移。

## 3. 拆掉什麼

| 拆掉 | 為什麼可以拆 |
|---|---|
| `_filter_rows_to_workspace` | 範圍已在 SQL 層限住，執行後再濾一次沒有增加保證，只增加一條靜默丟列的路徑 |
| `validate_scoped_narrative_sql` 的彙總封鎖 | 該限制存在的唯一理由是 post-filter 修不了彙總 |
| 同函式的「必須回傳 patent_id／id」 | 那是 post-filter 需要的欄位，不是業務需求 |
| `NARRATIVE_WORKSPACE_ID_ENV` | 併入單一變數 |

⚠ `validate_scoped_narrative_sql` 這個函式本身若還有其他呼叫端，只拆內容不拆函式。

## 4. 行為變更（要在驗收時講清楚）

對**綁定 workspace 的 narrative 任務**：

- **放寬**：彙總從「一律拒絕」→「JOIN `workspace_scope` 即可」
- **收緊**：查 `patents`／`patent_attributes` 沒 JOIN → 從「靜默過濾」變成「拒絕」

⚠ 收緊那條會讓既有 prompt 產生的某些查詢開始被拒。這是刻意的：那些查詢原本
拿到的是被靜默砍過的結果，使用者無從得知砍了什麼。錯誤訊息含改寫範例，
CLI 收到後可自行修正。

## 5. 測試要改的既有斷言

| 既有測試 | 現在斷言 | 改成 |
|---|---|---|
| `test_scoped_query_database_rejects_aggregate_sql` | 彙總被拒 | 彙總 **JOIN 後可執行**；未 JOIN 才被拒 |
| `test_scoped_query_database_requires_patent_identity` | 必須含 patent_id | 移除（post-filter 的需求，非業務需求） |
| `test_scoped_row_filter_keeps_only_workspace_patents` | post-filter 行為 | 移除，改由「CTE 注入 ＋ 閘門」測試覆蓋 |
| `test_narrative_report_scope_restores_environment` | 還原兩個環境變數 | 保留，但 workspace 那個改成統一變數 |
| `test_run_narrative_scopes_research_tools_to_workspace` | runner 有綁 scope | 保留，改驗統一入口 |

⚠ 移除測試前先確認它守的東西已被別的測試守住——`test_scoped_row_filter…` 守的是
「不得回傳別的 workspace 的專利」，改由「未 JOIN 即拒絕」＋「JOIN 後彙總正確」兩條承接。

## 6. 驗收判準

1. 全 repo 只剩一個 workspace scope 環境變數常數（以符號搜尋確認）。
2. 綁定後未 JOIN → 拒絕，訊息含 `workspace_scope` 與改寫範例。
3. 綁定後 `SELECT count(*) … JOIN workspace_scope` → 可執行，且數字＝該 workspace。
   ⚠ 這條要**真的比對數字**，不能只驗「沒有拋錯」。
4. 空 workspace → 拒絕。
5. 未綁定 → 與原本一般查詢逐字相同（同一句 SQL 進出不變）。
6. 兩份 prompt 文件不再出現「不得彙總」語意。
7. `narrative_report_scope` 仍正確還原 snapshot 環境變數。
