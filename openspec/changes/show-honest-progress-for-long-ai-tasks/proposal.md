## Why

長時間 AI CLI 工作目前可能長時間停在同一百分比，讓使用者誤以為系統卡住或誤信一個無法量測的進度。系統需要顯示真實 stage、heartbeat 與已執行時間，對不可預估的 CLI 階段採不確定進度，而不是偽造完成比例。

## What Changes

- 對可量測工作保留真實百分比；對不可量測的 AI CLI 階段顯示 indeterminate 狀態與 elapsed time。
- 以 heartbeat 與 stage transition 區分「仍在執行」和「可能失聯」，不得因百分比未變就判定失敗。
- SSE 與 polling 使用同一份進度投影，重連後可恢復目前 stage、elapsed time 與終結狀態。
- 多筆同時工作維持各自的開始時間、stage 與狀態，不互相覆蓋。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `platform-runtime`：長時間 AI 工作以誠實、可恢復的進度語意呈現。

## Scope

工作狀態投影、AI runner heartbeat/stage metadata、SSE／polling payload 與前端共用任務進度元件。

## Non-goals

- 不宣稱能預測外部 CLI 的剩餘時間。
- 不重做工作佇列、SSE transport 或 AI job routing。
- 不把 log 全文當作進度事件傳到前端。

## Impact

主要影響 job result/progress contract、AI Companion runner、任務 API/SSE 與 `backend/app/static/index.html`；不需 DB schema 或資料搬移，既有工作狀態須維持向後相容。

## Activation

部署 backend、AI Companion 與前端後生效；若舊 worker 未提供新 metadata，前端須安全降級為既有狀態文字，不偽造百分比。

## Acceptance Gate

以可控制的短／長 AI fake runner 驗證 heartbeat、stage、elapsed time、SSE 斷線重連、polling fallback、同時多任務與成功／失敗終結；瀏覽器實機觀察期間不得出現假百分比、負 elapsed time 或任務互相覆蓋。
