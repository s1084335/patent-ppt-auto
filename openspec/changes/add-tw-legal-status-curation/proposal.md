## Why

WIPS 的 `状态[US,JP,KR,CN,EP,CA,AU]` 不涵蓋 TW，導致臺灣專利的 `legal_status` 長期為空，既有「專利狀態分析」也無法呈現完整國別分布。本變更提供受控的人工首次登錄流程，讓內網使用者補齊 TW 狀態，同時確保狀態資料只影響專利狀態分析而不改變分群結果。

## Intent

建立一個集中、預設收合、只處理尚未登錄狀態之 TW 專利的單筆管理流程；狀態寫入後於背景刷新專利狀態分析，畫面不導頁，且任何結果都不得觸發或影響分群。

## What Changes

- 在 `core_layer.patents` 保存目前 `legal_status`，並以同表 JSONB 欄位保存首次登錄歷程；不新增 table。
- 提供只列出 `country_code='TW'` 且 `legal_status` 空白之專利的查詢，以及只允許空值首次寫入的原子更新 API。
- 前端新增預設收合的「TW 專利狀態管理」區塊，採九項下拉選單與單筆儲存；成功後該列立即移除。
- 儲存後排入背景工作，只刷新「專利狀態分析」；刷新失敗保留已登錄狀態並提供重試，不切換畫面。
- 將九項顯示狀態映射為 `pending`、`alive`、`dead`、`unknown`，但該分類只供專利狀態分析使用。
- 明確禁止 `legal_status` 或其彙總分類成為分群輸入、篩選、排除條件或重新分群觸發來源。

## Scope

- TW、目前狀態為空、單筆首次登錄。
- 九項值域：`已申請`、`已公開`、`審查中`、`已核准`、`放棄`、`核駁`、`撤回`、`已失效`、`屆滿失效`。
- 歷程每筆只含 `from_status`、`to_status`、`changed_at`；時間由伺服器產生。
- 所有可經 Nginx 進入專利工具的內網使用者皆可操作，不新增角色權限。
- 背景刷新、非阻塞狀態提示、失敗重試與分群隔離。

## Non-goals

- 不做批次修改、查看全部 TW 專利或修改已登錄狀態。
- 不新增狀態歷程 table，不保存 `changed_by`，不要求狀態生效日。
- 不新增登入、角色或個人身分識別系統。
- 不解析 `DOCDB法律状态`，不自動推論 TW 法律狀態。
- 不重跑分群、不改 cluster assignment、不刷新其他報表。

## Confirmed Decisions

- 詳細狀態保存於既有 `patents.legal_status`，專利無論狀態為何都持續納入分析。
- 歷程保存於 `core_layer.patents` 的 JSONB 欄位，不新增 table。
- 管理區只顯示尚未登錄狀態的 TW 專利，預設收合，且每次只處理一筆。
- 儲存成功後背景刷新專利狀態分析；畫面留在原處。
- 刷新失敗不回滾狀態，使用者可重試刷新。
- 所有內網使用者皆可操作；不記錄操作者。

## Open Questions

無阻塞問題。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `patent-data-model`: 在 `core_layer.patents` 保存狀態目前值與 JSONB 登錄歷程，並以 Alembic 管理 migration 與 rollback。
- `patent-ingestion`: 明確保證 TW 匯入空狀態不得覆蓋人工值，人工值也不得回寫 raw source。
- `workspace-and-browse`: 新增集中、收合、只列待登錄 TW 專利的單筆管理 API 與前端互動。
- `patent-reporting`: 詳細狀態只供專利狀態分析彙總，儲存後背景刷新且失敗可重試。
- `clustering-and-topics`: 明確排除 `legal_status` 及其彙總分類對分群輸入、篩選、排除與觸發的影響。

## Impact

- DB：新增 `core_layer.patents.legal_status_history JSONB NOT NULL DEFAULT '[]'::jsonb`；既有列只套預設空陣列，不搬移或推導歷史。migration 必須驗證 upgrade、既有資料保存、default/NOT NULL、downgrade，且不得建立新 table。
- API/repository：新增待登錄 TW 查詢、九項白名單驗證、只允許空值的原子更新與歷程 append；重複或併發第二次提交須回傳衝突。
- Frontend：新增預設收合的管理區、單筆下拉與儲存、成功移除、非阻塞背景刷新狀態及失敗重試。
- Reporting：刷新既有專利狀態分析所需資料與圖表；不刷新其他 report key。
- Ingestion：沿用「incoming 空值不覆蓋既有非空值」護欄；不需重匯來源檔。
- Clustering：須有防回歸驗收證明狀態更新前後 assignments 不變且未建立 clustering job。

## Activation

- 部署包含 Alembic migration、backend/API、worker 與 frontend 後生效。
- migration 不回填既有 `legal_status`，既有非空狀態保持原值；TW 空值由使用者逐筆登錄。
- 不需要重匯 WIPS、不需要重跑 embeddings 或分群。

## Acceptance Gate

- OpenSpec strict validation 通過，且 proposal、delta specs、design、tasks 互相一致。
- TDD 證明 migration、九項值域、TW/空值限制、原子更新、歷程內容與併發衝突。
- 前端實機驗收預設收合、只列待登錄 TW、單筆成功移除、畫面不跳轉與錯誤保留。
- 組合驗收證明背景刷新只更新專利狀態分析；失敗不回滾且可重試。
- 分群防回歸證明狀態登錄前後 assignments 不變，且沒有建立 clustering job。
- 使用目標 PostgreSQL 驗證 migration 後無新增 table，並驗證 downgrade 契約。
