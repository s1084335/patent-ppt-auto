# Tasks: unify-workspace-scope

⚠ 分支 `feat/unify-workspace-scope`，自 `feat/add-deck-delivery-line` 的 `d5abef0` 分出
（兩套機制在那次合併後才並存於同一份程式）。

⚠ 行為變更要在驗收時揭露：綁定 workspace 時，查 patent 級表未 JOIN `workspace_scope`
從「靜默過濾」變成「被拒絕」。

---

## 1. Red

- [x] 1.1 綁定後未 JOIN patent 級表 → 拒絕，訊息含 `workspace_scope` 與改寫範例
- [x] 1.2 綁定後 `count(*)` JOIN `workspace_scope` → **可執行**（原本一律拒絕）
- [x] 1.3 綁定後彙總結果**只涵蓋該 workspace**（真的比數字，不是只驗沒拋錯）
- [x] 1.4 空 workspace → 拒絕，不得退回全庫
- [x] 1.5 未綁定 → SQL 進出逐字不變
- [x] 1.6 `narrative_report_scope` 仍還原 snapshot 環境變數
- [x] 1.7 全 repo 只剩一個 workspace scope 環境變數常數

## 2. Green

- [x] 2.1 `narrative_report_scope` 的 workspace 部分委派給 `workspace_scope_env`
      ⚠ 委派，不是兩邊都設——兩邊都設等於保留兩個定義處
- [x] 2.2 `query_database` 只留 CTE 注入路徑
- [x] 2.3 移除 `_filter_rows_to_workspace` 與 `_workspace_patent_ids`
- [x] 2.4 移除 `validate_scoped_narrative_sql` 的彙總封鎖與 patent_id 強制
      ⚠ 先確認該函式沒有其他呼叫端；有的話只拆內容不拆函式
- [x] 2.5 移除 `NARRATIVE_WORKSPACE_ID_ENV`

## 3. 既有測試改寫

- [x] 3.1 `test_scoped_query_database_rejects_aggregate_sql` → 改為「未 JOIN 才拒絕」
- [x] 3.2 移除 `test_scoped_query_database_requires_patent_identity`
- [x] 3.3 移除 `test_scoped_row_filter_keeps_only_workspace_patents`
      ⚠ 移除前確認它守的「不得回傳別 workspace 的專利」已由 1.1＋1.3 承接
- [x] 3.4 `test_narrative_report_scope_restores_environment` 改驗統一變數
- [x] 3.5 `test_run_narrative_scopes_research_tools_to_workspace` 改驗統一入口

## 4. 文件

- [x] 4.1 `prompts/data_access.md`：「不得彙總」改為「彙總要 JOIN `workspace_scope`」
- [x] 4.2 `prompts/report-narrative-flow.md`：同上
- [ ] 4.3 ⚠ 回寫 `sync-report-contracts-and-palette` 的「母體閘門」項：
      本 change 已把 narrative 那條 prompt 規則換成機制，該項的起點變了

## 5. 驗收

- [x] 5.1 逐項對 design §6 的七條判準
- [x] 5.2 範圍回歸（直接／整合／契約）＋符號反查消費者
- [ ] 5.3 揭露行為變更與未覆蓋範圍
- [ ] 5.4 使用者接受後 archive；同步 main specs

---

## 完成狀態（2026-08-18）

- 1.x–3.x、4.1／4.2 完成；`test_unified_workspace_scope.py` 新增，既有 scope 測試改寫
- 目標測試 71 passed（unified／report_research_profile／ai_narrative_runner）
- **實庫實測 4／4**（唯讀）：成員數 226 = 宣告數；綁定後 `count(*)` 得 **226 而非全庫 281**；
  未 JOIN 的彙總被拒；未綁定時不改寫
  ⚠ 取樣時要挑成員數小於全庫的 workspace——全庫 workspace 比不出差異，那種比對是空的
- 4.3（回寫 `sync-report-contracts-and-palette` 的母體閘門項）留到該 change 開工時做，
  避免現在改到另一條分支的檔

### 尚未執行

- [ ] 4.3 回寫 sync-report-contracts-and-palette 的母體閘門起點
- [ ] 5.3 揭露行為變更（彙總放寬、未 JOIN 收緊為拒絕）
- [ ] 5.4 使用者接受後 archive；同步 main specs