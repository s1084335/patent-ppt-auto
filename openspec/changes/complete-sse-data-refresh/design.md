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

## Risks And Migration

- 風險：完成事件與 transaction commit 次序錯誤；只在 commit/persistence 成功後發布 succeeded。
- 風險：刷新風暴；採 resource 級 debounce 與 in-flight 合併。
- 遷移：先補事件契約測試，再逐頁接 invalidation，未接頁面仍保留手動刷新。
