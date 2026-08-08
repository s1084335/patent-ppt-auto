## Context

詳見 [proposal.md](proposal.md)。現況證據：WIPS mapping 的狀態欄不涵蓋 TW；`legal_status.py` 已是四類正規化的唯一定義處；專利 API 與前端目前只有狀態讀取；`lifecycle` 經 `report_generate` 產出且讀取 `derived_layer.report_patent_base`；現行 `refresh_derived` 會連家族 derived 一起全量重建；分群來源由 `backend/app/clustering/sources.py` 管理。

## Goals / Non-Goals

**Goals:**

- 以最小 schema 變更支援 TW 狀態首次登錄與歷程。
- 原子地收斂 core、單筆報表投影與狀態分析刷新。
- 以單一值域／分類來源避免 Python、JavaScript 與報表邏輯漂移。
- 保證狀態資料與分群相依圖完全隔離。

**Non-Goals:**

- 不建立通用 audit framework、個人身分、狀態更正流程或法律事件時間軸。
- 不執行全量 derived refresh，不新增 job type。

## Architecture And Data Flow

```text
Browser / Nginx
  -> GET pending TW patents + allowed statuses
  -> conditional status write (single patent, current workspace)
       -> one DB transaction
          1. conditionally update core_layer.patents
          2. set legal_status and append JSONB history
          3. sync this patent's report_patent_base.legal_status
       -> enqueue report_generate([lifecycle], current workspace)
  <- saved, refresh_status, refresh_job_id?

Worker -> generate only lifecycle/status-analysis artifact
No edge to embeddings, clustering, topics, or other report keys.
```

跨行程唯一交換媒介為既有 `app_layer.workflow_runs`／`workflow_outputs`。backend enqueue `report_generate`，worker 只依 payload 取得 workspace 與單一 report key；不得使用程序記憶體、檔案旗標或新 queue。

## Decisions

### 1. 歷程與目前值同列保存

新增 `core_layer.patents.legal_status_history JSONB NOT NULL DEFAULT '[]'::jsonb`。目前值繼續使用可查詢的 `legal_status`；歷程只供追溯且本階段最多一筆，不作報表來源。新增 history table 已由使用者否決；`workflow_outputs` 是工作產出版本，也不適合承載專利主資料歷程。

### 2. 只允許首次登錄的條件式原子更新

repository 在單一交易中限制 `country_code='TW'` 且 `NULLIF(BTRIM(legal_status), '') IS NULL`，同時更新目前值、append JSONB 並同步該 patent 的 report projection。影響列數為零時分辨不存在、非 TW 與已登錄，回傳 not-found／validation／conflict。先 SELECT 再 UPDATE 會產生競態，因此不採用。

### 3. 狀態清單與分類只有一個後端來源

九項值域與四類對照放在 `backend/app/mappings/legal_status.py`。待登錄查詢回應從該來源產生 `allowed_statuses`，前端只渲染回應；狀態分析呼叫同一 mapping，不得複製常數。

### 4. 只同步單筆報表投影

狀態分析讀 `derived_layer.report_patent_base`，故 core 更新交易同步該 patent 的 `legal_status` 投影。此操作只複製可由 core 重建的同名欄位；未來全量 refresh 應得到相同結果。enqueue `refresh_derived` 會重建家族資料，超出需求，因此不採用。

### 5. 沿用 report_generate 且隔離刷新失敗

資料交易成功後以 `report_generate`、`report_names=['lifecycle']`、目前 `workspace_id` enqueue。enqueue 或 worker 失敗不得回滾資料；API 分別回報 `saved` 與 `refresh_status`。重試只重建該報表，不再呼叫狀態寫入。

### 6. 權限沿用內網入口

所有經 Nginx 進入工具的內網使用者都可操作，不新增 role 或個人識別，也不接受 client 傳入 `changed_by`。直接暴露 backend 時仍受既有 deployment security 規格約束。

### 7. 分群隔離採結構與測試雙重保護

狀態 endpoint 只依賴 patent repository、targeted projection 與 report job repository，不 import 或呼叫 clustering／embedding service。驗收同時檢查沒有建立相關 job、assignment 不變，以及 clustering input/freshness identity 不變。

## Program Locations

- 值域與 mapping：`backend/app/mappings/legal_status.py`
- Migration：`alembic/versions/`
- Query/write：`backend/app/app_layer/patent_queries.py` 或相同 ownership boundary 的專利狀態 repository；router 不直接寫 SQL
- API：`backend/app/api/patents.py`
- Targeted projection：`backend/app/derived/`
- Queue：`backend/app/db/job_repository.py` 與現有 reports API/job contract
- Report：`backend/app/reports/report_definitions.py`、`chart_runner.py`
- Frontend：`backend/app/static/index.html`

## Output Contract

- Pending query：`items`、`total`、`limit`、`offset`、`allowed_statuses`；item 至少含 `patent_id`、顯示用專利號、標題及 `legal_status`。
- Write success：`saved=true`、更新後狀態、`refresh_status=queued|enqueue_failed`，可用時含 `refresh_job_id`。
- 已登錄回 conflict；非法值與非 TW 不得修改資料。
- Retry 只建立新的狀態分析 refresh job，不新增 history。

## Test Mapping

- DAT-007：upgrade、default、NOT NULL、no-new-table、data preservation、downgrade。
- ING-011：空 incoming 不覆蓋人工值、raw 與 history 不變。
- WSP-012：filter、九項契約、非法/非 TW/conflict、併發、UI 收合與列移除。
- RPT-009：mapping、targeted projection、單 report enqueue、失敗保留與 retry。
- CLU-012：job spy、assignment snapshot、input/hash 前後一致。

## Risks / Trade-offs

- [JSONB 不適合大量事件] -> 本階段只允許首次登錄；未來開放多次修改須另提 change。
- [core 成功但 enqueue 失敗] -> 回應 `enqueue_failed` 並提供獨立重試，不回滾也不誤報刷新完成。
- [targeted projection 與全量 refresh 分岔] -> 只複製 `core.legal_status`，測試斷言全量 refresh 結果一致。
- [前端複製狀態清單] -> API 提供 options，frontend contract test 禁止硬編碼第二份。
- [另一 worktree 已開始 lifecycle 實作] -> apply 前先與本 specs 做差集稽核；不一致先回寫規格。

## Migration Plan

1. 先建立 migration contract tests，再新增 JSONB 欄位；既有 rows 預設 `[]`，不回填歷史。
2. 依 TDD 完成 mapping、repository/API、targeted projection、前端及背景刷新。
3. 測試 DB 依序跑 upgrade、功能測試、downgrade、再 upgrade；測試資料未經使用者同意不得清除。
4. 部署 backend/worker/frontend 並執行 migration；不重匯、不重跑 embeddings 或分群。
5. Rollback 先停用 UI/API，再 downgrade 移除 history 欄；已寫入 `legal_status` 保留。

## Open Questions

無。
