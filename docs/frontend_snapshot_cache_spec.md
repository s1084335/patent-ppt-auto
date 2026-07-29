# Frontend Snapshot Cache Spec

## Summary Card

- 更新：2026-07-30
- 狀態：規格草案，尚未實作
- 已定案：瀏覽專利與分類區先讀前端快照；只有 refresh、操作提交、長任務完成後才向後端同步新快照
- 現況：目前 DB 在 Supabase，backend / worker 在 Lightning；正式上線會搬到公司伺服器
- 下一驗收點：進入瀏覽專利與分類區時，不依賴即時後端查詢也能先顯示上一份成功快照
- 詳情：本文件定義 snapshot schema、前後端流程、API 契約、快取失效與驗收方式

## 目標

瀏覽專利與分類區目前每次進入頁面都依賴後端查詢，使用者會看到 loading。目標是改成「前端先顯示快照，後端只在需要時同步」：

1. 進入瀏覽專利或分類區時，先讀 snapshot 並立即渲染。
2. backend / worker 暫時斷線時，仍顯示上一份成功 snapshot。
3. 使用者按 refresh 時才查 Supabase 或讀取最新 derived data，成功後產生新 snapshot。
4. 使用者送出會改資料的操作時，後端必須在操作成功後更新相關 snapshot，前端再重載新 snapshot。
5. 長任務不阻塞畫面；任務完成後再切換到新 snapshot。

本規格不是完整 offline app，只是讀取層快照化。所有會改資料的操作仍必須送後端。

## 範圍

第一階段納入：

- 瀏覽專利：`GET /api/v1/patents` 對應的清單、分頁、搜尋結果與基本統計。
- workspace 專利清單：`GET /api/v1/workspaces/{workspace_id}/patents` 對應的成員專利清單。
- 分類區 topic 列表：`GET /api/v1/workspaces/{workspace_id}/topics` 對應的 topic 狀態。
- topic 專利列表：`GET /api/v1/workspaces/{workspace_id}/topics/{topic_key}/patents` 對應的 topic 成員。
- clustering 候選與完成狀態：`GET /api/v1/clustering/candidates`、`GET /api/v1/clustering/runs/{run_id}/candidates` 的 read-only payload。

第一階段不納入：

- 匯入 WIPS 檔案。
- 重新分群、incremental clustering、finalize 等長任務的即時結果。
- 公司名稱確認、topic rename、merge / unmerge、排除專利等寫入操作本身。
- PPT 產生、報表產生、AI narrative 產生。

不納入的功能可以在完成後觸發 snapshot 更新，但功能本身不改成前端本地操作。

## 架構原則

### 讀取快照

前端進頁面的資料來源順序：

1. 先找記憶體中的 snapshot。
2. 沒有記憶體 snapshot 時，讀 `localStorage` 或 `IndexedDB` 的上一份成功 snapshot。
3. 若本地沒有 snapshot，才打 backend 的 snapshot endpoint。
4. 若 snapshot endpoint 也失敗，顯示可理解的空狀態與錯誤，不讓頁面卡在 loading。

### 後端同步

後端負責生成 snapshot。前端不得自行組裝會影響一致性的核心資料。

同步流程：

```text
frontend refresh / operation
→ backend read/write DB
→ backend regenerate affected snapshot
→ backend return snapshot_version + snapshot_url
→ frontend load snapshot_url
→ frontend replace visible state
```

### 操作成功的定義

會改資料的 API 不能只以「DB 寫入成功」作為成功定義。若該操作會影響瀏覽專利或分類區畫面，成功回應必須代表：

- DB 寫入已完成。
- 相關 derived data 已更新，或已明確排入 job。
- 相關 snapshot 已更新；若是長任務，則 job 完成時更新。
- 回應帶回可重載的 `snapshot_version` 或 `snapshot_url`。

## Snapshot 類型

### browse_patents

用途：一般瀏覽專利清單。

建議 key：

```text
browse_patents:{query_hash}
```

`query_hash` 由 `keyword`、`limit`、`offset`、排序條件與其他篩選條件產生。不同查詢不得共用同一份 snapshot。

最小 schema：

```json
{
  "snapshot_type": "browse_patents",
  "snapshot_schema_version": 1,
  "snapshot_version": "20260730T100000.000Z-0001",
  "generated_at": "2026-07-30T10:00:00+08:00",
  "source": {
    "database": "supabase",
    "endpoint": "/api/v1/patents",
    "query": {
      "keyword": null,
      "limit": 100,
      "offset": 0
    },
    "query_hash": "sha256:..."
  },
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

### workspace_patents

用途：特定 workspace 的專利清單。

建議 key：

```text
workspace_patents:{workspace_id}:{query_hash}
```

最小 schema：

```json
{
  "snapshot_type": "workspace_patents",
  "snapshot_schema_version": 1,
  "snapshot_version": "20260730T100000.000Z-0001",
  "generated_at": "2026-07-30T10:00:00+08:00",
  "source": {
    "database": "supabase",
    "endpoint": "/api/v1/workspaces/{workspace_id}/patents",
    "workspace_id": 1,
    "query": {
      "keyword": null,
      "limit": 100,
      "offset": 0
    },
    "query_hash": "sha256:..."
  },
  "data": {
    "workspace_id": 1,
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

### classification_topics

用途：分類區 topic 列表與 topic 到專利的顯示入口。

建議 key：

```text
classification_topics:{workspace_id}:{source_field}:{cluster_run_id}
```

最小 schema：

```json
{
  "snapshot_type": "classification_topics",
  "snapshot_schema_version": 1,
  "snapshot_version": "20260730T100000.000Z-0001",
  "generated_at": "2026-07-30T10:00:00+08:00",
  "source": {
    "database": "supabase",
    "workspace_id": 1,
    "source_field": "wips_independent_claims",
    "cluster_run_id": 123,
    "topic_state_version": "..."
  },
  "data": {
    "topics": [],
    "total": 0
  }
}
```

### topic_patents

用途：單一 topic 的專利列表。

建議 key：

```text
topic_patents:{workspace_id}:{source_field}:{cluster_run_id}:{topic_key}:{query_hash}
```

最小 schema：

```json
{
  "snapshot_type": "topic_patents",
  "snapshot_schema_version": 1,
  "snapshot_version": "20260730T100000.000Z-0001",
  "generated_at": "2026-07-30T10:00:00+08:00",
  "source": {
    "database": "supabase",
    "workspace_id": 1,
    "source_field": "wips_independent_claims",
    "cluster_run_id": 123,
    "topic_key": "T001",
    "query_hash": "sha256:..."
  },
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

## API 規格

第一階段新增 snapshot API，保留現有 read API 不動。

```text
GET /api/v1/snapshots/patents
POST /api/v1/snapshots/patents/refresh
GET /api/v1/snapshots/workspaces/{workspace_id}/patents
POST /api/v1/snapshots/workspaces/{workspace_id}/patents/refresh
GET /api/v1/snapshots/workspaces/{workspace_id}/classification
POST /api/v1/snapshots/workspaces/{workspace_id}/classification/refresh
GET /api/v1/snapshots/workspaces/{workspace_id}/topics/{topic_key}/patents
POST /api/v1/snapshots/workspaces/{workspace_id}/topics/{topic_key}/patents/refresh
```

`GET` 只讀既有 snapshot，不做昂貴查詢。若找不到 snapshot，可以回 `404 snapshot_not_found`，前端再決定是否顯示空狀態或提示使用者 refresh。

`POST refresh` 才重新查 DB 或 derived data，成功後寫入新 snapshot。

標準 refresh 回應：

```json
{
  "snapshot_type": "workspace_patents",
  "snapshot_version": "20260730T100000.000Z-0001",
  "snapshot_url": "/api/v1/snapshots/workspaces/1/patents?snapshot_version=20260730T100000.000Z-0001",
  "generated_at": "2026-07-30T10:00:00+08:00"
}
```

## 前端狀態

前端狀態不得只有 `loading`。至少分成：

- `snapshot_ready`：已用 snapshot 顯示資料。
- `refreshing`：正在更新，但畫面保留舊 snapshot。
- `fresh`：已切到 refresh 後的新 snapshot。
- `stale`：後端不可用或 refresh 失敗，畫面仍顯示舊 snapshot。
- `empty`：沒有任何 snapshot，也無法取得資料。

`refreshing` 時不得清空表格。只在工具列或狀態列顯示「更新中」。

## Cache 與版本

snapshot URL 必須能避開瀏覽器、proxy 與 service cache 的舊資料問題。可接受兩種方式：

1. versioned URL：

```text
/api/v1/snapshots/workspaces/1/patents?snapshot_version=20260730T100000.000Z-0001
```

2. immutable static file：

```text
/static/snapshots/workspaces/1/patents/20260730T100000.000Z-0001.json
```

第一階段建議用 API version query，比較容易接現有 FastAPI。正式上公司伺服器後，可再改為 static file 或 shared volume 路徑。

固定 URL，例如 `/snapshots/workspace-patents.json`，必須搭配 `Cache-Control: no-store`，否則容易吃到舊資料。

## 寫入操作後的同步規則

所有會影響瀏覽專利或分類區顯示的寫入 API，必須明確宣告會 invalid 哪些 snapshot。

同步小操作：

- topic rename
- topic merge / unmerge 若後端可同步完成
- restore excluded patents
- confirm exclusion reviews
- keep exclusion reviews
- 公司名稱確認若會影響專利清單顯示名稱

標準流程：

```text
POST /api/v1/...
→ write DB
→ regenerate affected snapshots
→ return affected_snapshots[]
→ frontend reload affected snapshots
```

回應範例：

```json
{
  "status": "succeeded",
  "affected_snapshots": [
    {
      "snapshot_type": "classification_topics",
      "snapshot_version": "20260730T100010.000Z-0002",
      "snapshot_url": "/api/v1/snapshots/workspaces/1/classification?snapshot_version=20260730T100010.000Z-0002"
    }
  ]
}
```

長任務：

- import
- clustering calibrate
- clustering incremental
- clustering auto
- clustering finalize
- AI label
- patent notes
- irrelevant filter

標準流程：

```text
POST operation
→ return job_id
→ frontend keep old snapshot
→ poll /jobs/{job_id} or listen SSE
→ job succeeded
→ backend has generated affected snapshots
→ frontend reload affected snapshots
```

job 成功 payload 應帶：

```json
{
  "job_id": 10,
  "status": "succeeded",
  "result": {
    "affected_snapshots": [
      {
        "snapshot_type": "workspace_patents",
        "snapshot_version": "20260730T100100.000Z-0003",
        "snapshot_url": "/api/v1/snapshots/workspaces/1/patents?snapshot_version=20260730T100100.000Z-0003"
      }
    ]
  }
}
```

## Snapshot 儲存

目前開發部署：

- DB：Supabase
- backend / worker：Lightning

第一階段可由 backend 生成 snapshot，儲存在 backend 可讀寫的本地目錄或 shared volume：

```text
/app/output/snapshots/
```

若 worker 會產生 snapshot，backend 與 worker 必須共用同一個 snapshot volume。

正式公司伺服器部署時，snapshot 儲存不可寫死 Lightning 路徑。應抽成設定：

```text
SNAPSHOT_STORE=filesystem
SNAPSHOT_ROOT=/app/output/snapshots
SNAPSHOT_PUBLIC_BASE_URL=/api/v1/snapshots
```

未來若要換成 object storage，只替換 snapshot store，不改前端契約。

## 一致性規則

1. snapshot 必須標示 `generated_at`，前端要顯示資料時間。
2. 分類 snapshot 必須綁 `workspace_id`、`source_field`、`cluster_run_id`。
3. 不同 filter、keyword、pagination、sort 不得共用同一份 snapshot。
4. 寫入操作成功後，不得讓前端繼續顯示已知過期 snapshot 而沒有標示。
5. 若 refresh 失敗，前端保留舊資料並標示 `stale`。
6. 若 snapshot schema 升級，前端遇到不支援的 `snapshot_schema_version` 必須丟棄並要求 refresh。

## 實作順序

1. 定義 backend snapshot store 介面，先支援 filesystem。
2. 建立 snapshot serializer，先包現有 `patent_queries`、`workspace_queries`、topics repository 的 read payload。
3. 新增 snapshot GET / refresh API。
4. 前端瀏覽專利改為先讀 snapshot，再支援 refresh。
5. 前端分類區改為先讀 snapshot，再支援 refresh。
6. 寫入 API 加上 `affected_snapshots` 回應，至少先覆蓋 topic rename、merge / unmerge、排除專利確認。
7. 長任務成功結果加上 `affected_snapshots`。
8. 用 Playwright 驗證「進頁面先顯示快照」與「後端失敗仍保留舊資料」。

## 驗收標準

### 瀏覽專利

- 已有 snapshot 時，進入瀏覽專利不等待 `/api/v1/patents` 即時查詢即可看到表格。
- 按 refresh 後，舊表格保留，狀態顯示更新中。
- refresh 成功後，表格切到新 `snapshot_version`。
- refresh 失敗時，表格保留舊 snapshot，狀態顯示資料時間與 stale。

### 分類區

- 已有 snapshot 時，進入分類區不等待 topic 即時查詢即可看到 topic 列表。
- topic list 與 topic patents 使用同一個 `cluster_run_id`。
- topic rename 成功後，前端載入新 classification snapshot。
- merge / unmerge 成功後，前端載入新 classification snapshot。
- clustering job 完成前不清空舊 topic；完成後才切到新 snapshot。

### 斷線情境

- backend 不可連時，前端仍能顯示上一份本地 snapshot。
- backend 不可連時，refresh 顯示失敗但不清空資料。
- 沒有任何 snapshot 且 backend 不可連時，顯示空狀態與可重試操作。

### 快取情境

- 操作成功後重載的 snapshot URL 必須包含新 `snapshot_version`。
- Playwright 驗證同頁重載不會拿到舊 snapshot。

## 測試建議

後端測試：

- snapshot schema contract test。
- refresh API 產生新 snapshot 並回傳 `snapshot_version`。
- 不同 query 產生不同 `query_hash`。
- 寫入 API 回傳正確 `affected_snapshots`。
- job succeeded payload 包含 `affected_snapshots`。

前端測試：

- 有 local snapshot 時，render 不依賴即時 API。
- refresh 中不清空 table。
- refresh 失敗時保留舊資料並標示 stale。
- schema version 不支援時要求 refresh。

Playwright 驗收：

- 模擬 snapshot ready：進瀏覽專利可立即看到表格。
- 模擬 backend 500：畫面保留舊 snapshot。
- 執行 topic rename mock 成功：前端載入新 snapshot version。

## 風險與限制

- snapshot 不是最新 DB 本身，只是最後一次成功同步的資料。
- 若有外部程序直接改 DB 但沒有產生 snapshot，前端不會自動看到。
- snapshot 太大時，localStorage 不適合，需改 IndexedDB 或只存 manifest + 分頁 snapshot。
- 分類區若沒有綁定 `cluster_run_id`，topic 與 patent list 可能對不上。
- 若正式公司伺服器的 backend / worker 沒有 shared volume，worker 產生的 snapshot 會無法被 backend 提供給前端。

## 開放問題

1. 第一版前端本地儲存使用 `localStorage` 還是 `IndexedDB`？若單頁 patent snapshot 超過數 MB，應直接用 `IndexedDB`。
2. snapshot 是否要保留最近 N 版供 rollback / debug？第一版可只保留 latest + current。
3. 公司伺服器正式部署時，snapshot 是否由 Nginx 直接 serve static file，還是仍走 FastAPI？
4. 是否需要背景自動 freshness check？第一版建議不要阻塞畫面，只顯示資料時間並提供 refresh。
