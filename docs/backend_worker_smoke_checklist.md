# Backend / Worker 容器 Smoke 驗收清單

更新：2026-07-22  
狀態：第一版容器重建後驗收清單。  
範圍：Backend / Worker / PostgreSQL workflow queue / report / clustering / market evidence。  
不含：正式資料清理、migration 套用決策、案件比對逐要素比對、PPT 最終輸出。

## 原則

- 不寫死部署主機 port；以 `.env` 控制。
- 本機驗收預設可使用 `POSTGRES_HOST_PORT=5433`、`BACKEND_HOST_PORT=8000`。
- 容器內連線固定使用 compose service name：`postgres:5432`。
- migration 是明確一次性操作，不跟 backend / worker service startup 綁在一起。
- Smoke test 只驗「服務可運作與資料可追溯」，不清正式資料。
- 若任一步失敗，停止在該段修正，不跨模組擴張。

## 前置確認

1. `.env` 存在，且至少有：

```text
POSTGRES_PASSWORD=...
POSTGRES_DB=patent_ppt
POSTGRES_USER=postgres
POSTGRES_HOST_PORT=5433
BACKEND_HOST_PORT=8000
DATA_HOST_PATH=./data
OUTPUT_HOST_PATH=./output
CONTAINER_MODEL_ARTIFACT_ROOT=/app/data/model_artifacts
```

2. 確認 compose 設定可解析：

```powershell
docker compose config
```

通過標準：

- `postgres`、`backend`、`worker` 都存在。
- `backend` 與 `worker` 使用同一個 `${APP_IMAGE}`。
- host port 來自 env，不是硬編在程式。

## 第 1 關：DB / Migration 狀態

用途：確認容器 DB 可連、目前 migration 版本明確。

```powershell
docker compose up -d postgres
docker compose ps postgres
docker compose exec postgres psql -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-patent_ppt} -c "SELECT version_num FROM alembic_version;"
docker compose run --rm backend alembic current
```

通過標準：

- `postgres` 狀態為 healthy。
- `alembic_version` 與 `alembic current` 一致。
- 若未到 head，先停下交給 DB/migration 負責線決定，不在 smoke test 自行套正式 migration。

## 第 2 關：Backend 服務

用途：確認 FastAPI image 可啟動、ready endpoint 可區分 DB 與 worker 狀態。

```powershell
docker compose up -d backend
docker compose ps backend
Invoke-RestMethod "http://127.0.0.1:${env:BACKEND_HOST_PORT}/api/v1/ready"
Invoke-RestMethod "http://127.0.0.1:${env:BACKEND_HOST_PORT}/api/v1/jobs?limit=5"
```

若 PowerShell 未設定 `BACKEND_HOST_PORT`，本機預設：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/ready"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs?limit=5"
```

通過標準：

- backend container healthy。
- `/ready` 回傳 database ok。
- `/jobs?limit=5` 不應 500。

## 第 3 關：Worker Claim / Heartbeat

用途：確認 worker 可 claim job、更新 heartbeat、寫回 succeeded / failed。

操作方式：

1. 透過 API 建立一個最小 report 或 clustering job。
2. 啟動 worker。
3. 觀察 job 狀態變化。

```powershell
docker compose up -d worker
docker compose logs -f worker
```

另開終端查詢：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs?limit=10"
```

通過標準：

- job 從 `queued` 進到 `running`，最後 `succeeded` 或有可讀 `failed` 原因。
- `current_stage` 與 `progress_percent` 有更新。
- 大資料 job 執行中有中間 heartbeat，不因長時間 embedding / report 而被誤判 stale。

## 第 4 關：Report Job Smoke

用途：確認報表線可從 backend job 進 worker，產生版本化 artifact。

建議驗收內容：

- 建立 report job。
- 檢查 `workflow_runs` 狀態。
- 檢查 `workflow_outputs` 或既有 analysis/report output。
- 檢查 `output/full_report_latest` 或該次 version/run 目錄存在。
- 驗證同 workspace 重跑不覆蓋舊 version。

通過標準：

- report job succeeded。
- 產出資料包含 report version / filters / patent snapshot。
- 圖檔 artifact 有 sha256。
- 表格資料走 JSON / DB output，不依賴 CSV 作為正式資料交換。

## 第 5 關：Clustering Job Smoke

用途：確認分群線可在容器中使用同一 DB 與 artifact root。

建議驗收內容：

- 使用乾淨 workspace。
- 建立候選分群 job。
- 確認候選主題數範圍依資料量產生。
- finalize 選定候選方案。
- 檢查正式 topic、topic labels、representative patents。

通過標準：

- technical / effect 來源欄位分開處理。
- 代表性文檔每 topic 以 topic probability 取前 5 筆。
- 任意 split 不出現在第一版流程；只保留 merge history 還原。
- model artifact 使用相對 key，實體 root 由 `MODEL_ARTIFACT_ROOT` 決定。

## 第 6 關：Market Evidence Smoke

用途：確認 Claude CLI 市場研究結果不會跳過人工確認直接進正式表。

驗收順序：

1. 呼叫 `prepare_market_evidence_task` 取得可讀 task brief。
2. 呼叫 `save_market_evidence_candidates` 暫存候選 evidence。
3. 查 workflow output，確認 guard.accepted 為 false。
4. 使用者選定 index 後呼叫 `accept_market_evidence_candidates`。
5. 查正式 `market_evidence`。

通過標準：

- candidate 必須有 `source_url`。
- `payload_json.source_url` 必須等於外層 `source_url`。
- candidate 必須有 `payload_json.evidence_excerpt`。
- 未 accept 前不得出現在正式 `market_evidence`。

## 第 7 關：AI Narrative / Claude CLI 任務

用途：確認 backend / worker 能建立 AI 任務，但 CLI 執行與文字結果可追溯。

通過標準：

- job payload 中明確列出任務類型、輸入 report key、artifact key、結構化 rows。
- 失敗時寫入可讀錯誤，不吞 exception。
- 成功時 output 包含 `source_analysis_id`、`source_report_version`、`data_sources`。

## 第 8 關：Frontend Minimal Smoke

用途：確認最小前端可打真 API，不看假資料。

通過標準：

- `/` 可開啟 `backend/app/static/index.html`。
- workspace、job list、report latest、comparison 入口都能呼叫真 API。
- 若 API 回 404/422/500，畫面顯示可讀錯誤，不靜默失敗。

## 驗收回報格式

每輪容器 smoke 完成後回報：

```text
DB/Migration：
Backend：
Worker：
Report：
Clustering：
Market evidence：
AI narrative：
Frontend：
阻斷問題：
下一步：
```

## 停止條件

任一條件發生就停止，不繼續下一關：

- 正式 DB migration 狀態不明。
- 需要清正式資料。
- 需要新增或修改 DB schema。
- backend 或 worker healthcheck 連續失敗。
- job 卡在 running 且 heartbeat 不更新。
- artifact root 寫到容器臨時路徑，無法跨重建保留。
