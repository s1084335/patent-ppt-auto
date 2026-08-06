# Design: 前端快照快取

## Context

目前瀏覽、分類與報表內容由多個 API 即時組合，畫面重新整理時可能在同一個 workspace 讀到不同批次的資料。此 change 建立可版本化、可判斷過期的快照契約；它不改變資料庫中的專利事實，也不把前端快取當成權威資料源。

## Goals / Non-goals

- 目標：同一畫面週期使用相同 `snapshot_id`，並能以 `generated_at`、`cluster_run_id` 與內容版本判斷是否失效。
- 目標：瀏覽、分類與 latest report content 共用明確的快照識別與重新整理流程。
- 非目標：離線優先、跨 workspace 共用快取、取代後端報表 artifact。

## Decisions

### 1. 快照是 API 契約，不是另一份事實資料庫

後端回傳快照 envelope，至少包含 `snapshot_id`、`workspace_id`、`generated_at`、`cluster_run_id`、`content_version` 與 payload。前端只能以完整 envelope 寫入快取，不得拼接不同版本的 payload。

### 2. 失效條件由資料世代決定

workspace 切換、匯入完成、分群 run 改變、報表重新產生或 schema/content version 改變時，既有快照失效。TTL 只作保底，不能掩蓋資料世代已改變。

### 3. stale-while-revalidate 必須可觀察

若有可用舊快照，可先顯示並在背景更新；畫面必須保留 loading、stale、refreshing、error 四種狀態，不可把更新失敗顯示成最新成功。

## Architecture And Code Boundaries

- API：`backend/app/api/` 的 browse、classification、report latest content 路由。
- application/service：集中產生與驗證 snapshot metadata，避免每個 route 自訂規則。
- frontend：query/store 層保存 envelope；view 僅消費單一版本。
- persistence：只有需要跨程序重用時才增加資料表或 artifact；優先沿用既有 report artifact 與 cluster run id。

## Output Contract

- 成功：回傳完整 snapshot envelope 與可序列化 payload。
- 無資料：回傳明確 empty payload，仍帶版本資訊。
- 過期：前端標記 stale 並觸發 refresh。
- 失敗：保留最後成功版本但顯示 error；沒有成功版本時顯示空錯誤狀態。

## Test Strategy

- 單元測試：snapshot key、版本比較、失效條件、序列化。
- API 測試：同一世代 id 穩定、cluster/report 更新後 id 改變、空資料與錯誤契約。
- 前端測試：workspace 切換不串資料、stale refresh、競態時只接受最新請求。
- 驗收：瀏覽器實測 browse、classification、latest report content，保留 network 與畫面證據。

## Risks And Migration

- 風險：舊請求晚回覆覆蓋新 workspace；以 request generation/token 拒絕過時 response。
- 風險：快取 key 缺少 cluster/report 世代；由後端集中產生 key。
- 遷移：先讓 API 提供 metadata，再導入前端快取，最後移除各頁零散的 reload 邏輯；每階段保留無快取回退路徑。
