## Context

現有工作 API、SSE 與前端任務卡已能傳遞狀態與部分進度；AI Companion 另行領取 `AI_JOB_TYPES` 並執行外部 CLI。外部 CLI 多數階段沒有可驗證的總工作量，舊規劃卻可能以百分比呈現，與實際可觀察資訊不一致。跨 backend、Companion 與瀏覽器的唯一交換媒介維持 `workflow_runs` 狀態／進度投影及其 Job API/SSE payload，不另建第二套前端計時來源。

## Goals / Non-Goals

**Goals:**

- 讓可量測與不可量測階段有不同但一致的進度語意。
- SSE、polling 與重連都從同一權威工作投影恢復 stage、時間與狀態。
- 保留多任務隔離與現有 job lifecycle。

**Non-Goals:**

- 不估算外部 CLI 剩餘時間，不解析自然語言 log 猜完成度。
- 不新增 queue、event bus 或瀏覽器專屬工作狀態資料庫。

## Decisions

### 1. 進度模式是明確欄位，不由前端猜測

工作投影增加向後相容的 `progress_mode`（`determinate`／`indeterminate`）、`stage`、`started_at`／可推導 elapsed 的 server timestamp、`heartbeat_at`。舊 producer 缺欄位時，前端顯示狀態文字並隱藏百分比。未採「超過 N 秒自動改模式」，因等待時間不能可靠代表階段性質。

### 2. Elapsed time 以伺服器時間錨點計算

API/SSE 傳開始時間與事件 server time，瀏覽器只做顯示更新；重連或切頁後重新由權威值推導，不保存另一份可漂移的累加秒數。終結狀態保存完成時間並停止更新。

### 3. Heartbeat 與完成比例分離

Companion 在可安全回報的邊界更新 heartbeat/stage；heartbeat 只表示執行者仍存活，不代表完成比例。逾時判定沿用 worker lifecycle 的既有規則，UI 不自行把「百分比不動」升格成失敗。

### 4. 任務卡以 run identity 隔離

前端 registry 以 `run_id`／`job_id` 作 key，timer 只觸發重繪並從該筆狀態計算 elapsed；workspace 或頁面切換時清理畫面 timer，但不得改寫工作本身狀態。

## Architecture And Code Boundaries

- 工作 repository／API：保存並投影 progress mode、stage 與時間錨點。
- AI Companion runner：在 CLI 啟動、主要階段與終結點更新 stage/heartbeat。
- SSE／polling：序列化同一份工作投影，不各自定義欄位語意。
- `backend/app/static/index.html`：任務卡依 mode 呈現 determinate bar 或 indeterminate bar＋elapsed time。

## Test Mapping

- Repository/API contract：舊資料缺欄位、時間順序、terminal freeze、失聯與 heartbeat。
- Companion runner：CLI 啟動／執行／落 artifact／成功或失敗的 stage transition。
- Frontend contract：fake timers、SSE→polling、重連、兩筆同時任務、頁面切換與 timer cleanup。
- 整合：可控制的長時間 fake CLI 與一筆真實 CLI smoke，保存 timeline 與畫面。

## Output Contract

任務 payload 至少能表達 identity、status、progress mode、可選真實 percent、stage、started/server/heartbeat/completed time 與錯誤摘要。新欄位為 additive；舊 client 可忽略，舊 producer 缺值時新 client 必須安全降級。

## Risks / Trade-offs

- [用戶把 heartbeat 誤解為完成進度] → 文案只顯示「執行中」與 stage，不換算百分比。
- [客戶端時鐘偏差使 elapsed 異常] → 使用 server time 錨點並 clamp 為非負、單調顯示。
- [每秒重繪造成負擔] → 僅更新可見 running 任務，使用單一 timer registry。
- [舊 producer 沒新欄位] → 先做向後相容顯示，再逐 runner 補 metadata。

## Migration Plan

1. 先以 contract tests 固定 additive payload 與舊資料降級行為。
2. 補 Companion stage/heartbeat，再接 API/SSE 與前端呈現。
3. staging 同時跑新舊 job，確認不影響 lifecycle 與自動刷新。
4. 發生回歸時可先關閉新 UI mode，工作資料與既有 status/progress 欄仍可使用；不需 DB rollback 或資料重寫。
