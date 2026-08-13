# Design: SSE 資料自動刷新補完

## Context

現有 job event stream 能傳遞進度，但部分表格只更新狀態文字，完成事件後仍顯示舊資料。此 change 將「事件通知」與「權威資料重新查詢」分開：SSE 只宣告哪個資源可能改變，畫面再由既有 API 取得最新資料。

## Goals / Non-goals

- 目標：匯入、分群、AI 與報表工作完成後，受影響的 browse/table/report view 自動刷新一次。
- 目標：斷線重連、重複事件與 out-of-order event 不造成重複請求風暴或舊資料覆蓋。
- 非目標：把完整表格資料塞進 SSE、以 SSE 取代一般 API、保證跨瀏覽器 exactly-once。

## Decisions

### 1. 事件只攜帶 invalidation metadata

完成事件至少包含 `event_id`、`job_id`、`workspace_id`、`job_type`、`status`、`completed_at` 與受影響資源。資料內容仍由既有 endpoint 取得。

### 2. 刷新採去重與合併

前端以 `event_id` 去重，同一 workspace/resource 在短時間內的多個完成事件合併成一次 refresh。若 workspace 已切換，舊 workspace 事件不得更新目前畫面。

### 3. 重連必須能補事件或安全全量刷新

優先使用 Last-Event-ID/游標補送；若後端無法保證補送，重連成功後對目前 workspace 的可見資源做一次受控刷新。

## Architecture And Code Boundaries

- event producer/job lifecycle：產生 terminal event 與 resource invalidation metadata。
- SSE API：維持 heartbeat、游標與 workspace 權限邊界。
- frontend event client：連線、重連、去重與 dispatch。
- query/table layer：接收 invalidation 後呼叫原有 browse/report/topic API。

## Output Contract

- running/progress event 只更新進度。
- succeeded event 觸發受影響資源刷新。
- failed/cancelled event 更新狀態但不宣告資料已成功更新。
- 斷線時 UI 保留最後資料並呈現連線狀態；重連後完成補償刷新。

## Test Strategy

- 後端：event schema、terminal event、權限、heartbeat、Last-Event-ID。
- 前端：重複事件、亂序事件、workspace 切換、斷線重連、refresh 合併。
- 整合：逐一跑匯入、分群、AI 與報表 job，確認目標表格與 artifact 狀態無手動 reload 即更新。
- 驗收：瀏覽器 network/SSE timeline 與更新前後畫面截圖。

## 事件×刷新矩陣（2026-08-11 盤點定稿，task 1.1–1.3）

### 現況事實（實掃 `events.py`／`fd301dee99c3`／`index.html`）

- DB trigger `notify_run_change` 只送 `{kind:'run', run_id, status, progress, stage}`
  ——**缺 `run_type`／`workspace_id`**，前端無從判斷該刷新誰 → 需 migration 補欄。
- `pg_notify` 於 **COMMIT 時**才遞送（PostgreSQL 語意），「succeeded 只在 persistence
  成功後發布」由此天然成立，不需 producer 改碼。
- `pg_notify` **無法補送歷史事件** → Last-Event-ID／游標不做；斷線補償走
  「重連成功後對當前頁面可見資源做一次受控刷新」（決策 3 的 fallback 路徑）。
- 既有部分刷新（保留、不重做）：`ai:narrative` 成功 → `updateTaskFromEvent` →
  `fetchTasks` → `maybeRefreshReportNarratives`（帶 based_on_version 守門與
  job_id 去重）刷報表檢視區與匯出頁快取。30 秒輪詢保底沿用同一條。

### 事件契約（task 1.2）

`kind:'run'` payload：`event_id`（`run_id:status`，終結事件去重鍵）、`run_id`、
`run_type`、`workspace_id`（可為 null）、`status`、`progress`、`stage`、
`completed_at`（僅終結狀態帶）。`kind:'output'` 維持不變。心跳沿用 15s。

### job type → 資源（唯一來源落前端 `JOB_REFRESH_TARGETS`）

| job type | 刷新資源 |
|---|---|
| `patent_import` | browsePatents、noteCoverage、workspaces（頂列下拉，含新 workspace） |
| `refresh_derived` | browsePatents（顯示名收斂） |
| `ai:patent_note` | browsePatents、noteCoverage |
| `clustering_calibrate`／`clustering_incremental`／`clustering_finalize`／`topic_merge`／`topic_unmerge`／`ai:topic_label`／`ai:topic_backfill`／`ai:candidate_explanation`／`ai:irrelevant_filter` | topics |
| `report_generate` | reports（版本區＋檢視區，保留選單選擇） |
| `ai:narrative` | （走既有 maybeRefreshReportNarratives 路徑，不重複接） |
| `embeddings`／`ai:company_zh_name` | （無可見面）|
| `case_comparison` | 未覆蓋（比對頁自有輪詢；列入 task 3.4 揭露） |

資源 → 刷新函式與適用頁（gating）：browsePatents→`browse` 頁重載 `#browse-body`
並**保留已展開詳情列**（detail row 補 `data-pid`，重載後回開）；noteCoverage→
`renderNoteCoverage`；topics→`topics` 頁 `renderTopics()`（選擇存 state 不受重繪影響）；
reports→`reports` 頁 `loadReportVersions()`＋`reloadCurrentReportContentOnly()`；
workspaces→任何頁（頂列）重載下拉並保留當前選定。

### 異常行為（task 1.3）

- **重複事件**：終結事件以 `event_id` 去重（Set 上限 200 筆滾動）；進度事件不去重。
- **亂序**（terminal 後又收到 running）：資料刷新只認 `succeeded`，晚到的進度事件
  只動任務卡，不動資料區。
- **failed／cancelled**：更新任務卡與錯誤，不刷資料區。
- **斷線**：畫面保留最後資料，30 秒輪詢保底；**重連成功**後對當前頁可見資源
  做一次補償刷新＋`fetchTasks`。
- **workspace 切換**：刷新函式一律經「當前 state」呼叫權威 API，取回的本來就是
  當前 workspace 的資料——舊 workspace 事件最多造成一次多餘 fetch（有 debounce），
  不可能把舊資料畫上來（by construction）。
- **refresh API 失敗**：靜默保留舊資料（與既有 reload helpers 一致），下一個事件
  或手動操作自然恢復；不彈錯誤打斷操作。
- **合併**：同資源 1.5 秒 debounce；in-flight 中再收到事件則記一次待重跑，完成後補一輪。

## Risks And Migration

- 風險：完成事件與 transaction commit 次序錯誤；只在 commit/persistence 成功後發布 succeeded。
- 風險：刷新風暴；採 resource 級 debounce 與 in-flight 合併。
- 遷移：先補事件契約測試，再逐頁接 invalidation，未接頁面仍保留手動刷新。
