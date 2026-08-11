## Why

SSE 已能更新任務卡，但文獻備註、公司名、分類與報表等資料區塊仍可能停留在舊內容，使用者需要 F5 或切頁才能看到工作結果。

> ⚠ 2026-08-11 實測修正：「SSE 已能更新任務卡」**不成立**。實跑發現兩個既有斷點：
> ① DATABASE_URL 走 Supabase pooler :6543（transaction pooling），LISTEN 靜默收不到
> 任何 NOTIFY；② `notifies(timeout=0.5)` 語意是 generator 總壽命 0.5 秒，LISTEN
> 執行緒開場即死。任務卡過去其實靠 30 秒輪詢與頁面級輪詢在動。兩者已於本 change
> 修復（listen 連線改 session 模式＋外圈重進 generator），SSE 才第一次真正通。

## What Changes

- 依 job type 與終結狀態刷新真正受影響的資料區塊。
- 只在使用者位於相關頁面時刷新，並以 1–2 秒 debounce 合併連續事件。
- 保存表格展開、選取、收合與報表版本等互動狀態。
- 將 job type 到 refresh targets 的 mapping 收斂為唯一來源。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `platform-runtime`：SSE 完成事件從任務狀態更新擴充為資料刷新觸發。
- `workspace-and-browse`：受影響表格在背景工作完成後自動取得新資料且保留互動狀態。

## Scope

前端 `EventSource`、`updateTaskFromEvent`、資料區塊 reload helpers 與 job-to-target mapping。

## Non-goals

- 不重建既有 SSE backend 或 PostgreSQL trigger。
- 不在無關頁面預抓所有資料。

## Impact

主要是 `backend/app/static/index.html` 與前端契約測試；若 mapping 使用 backend metadata，需同步 API contract。

## Activation

前端檔案更新後需依實際部署方式重新載入；若 bind mount 使用現行檔案則不需重建 image。

## Acceptance Gate

逐一驗證匯入、備註、公司名、分群、排除、報表與匯出等受影響 job；停留無關頁不得誤刷，互動狀態不得重置。

