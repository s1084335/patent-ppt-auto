# Frontend Snapshot Cache Spec

> Legacy source：本檔只保留尚待逐條吸收的細部決策。權威變更規格、設計與任務位於 `openspec/changes/add-frontend-snapshot-cache/`；兩者衝突時以 OpenSpec 為準。

## Summary Card

- 更新：2026-08-03（三個開放問題已定案；v1 2026-07-30）
- 狀態：規格已定案，**尚未實作**
- 🔴 **2026-08-03 使用者定案**：
  1. **範圍**：先做**瀏覽專利＋分類區**（即原定 5 個讀取端點），驗收過再擴到其他區塊
  2. **更新粒度**：**區塊級**——每頁拆成獨立區塊，只重畫受影響的那一塊
     （搜尋框內容、捲動位置、已展開的列不得因為 refresh 而丟失）
  3. **快照儲存**：**後端檔案快照**（不做前端 IndexedDB）——多人共用同一份、
     不會各自不同步；代價是仍有一次網路往返，但不查 DB、只讀檔
- **使用者原話**（2026-08-03）：「所有功能都有其目的地生效的區域，確保這些功能每次
  完成都 refresh，只在最小範圍內 refresh(不要整頁)，現在每次點頁面都是載入，
  要做成快照映射到前端，這樣每次進到頁面就不用載入，有 refresh 就要隨即載入更新，
  避免有前後端不同步問題」
- 現況：目前 DB 在 Supabase，backend / worker 在 Lightning；正式上線會搬到公司伺服器
- 下一驗收點：進入瀏覽專利與分類區時，不依賴即時後端查詢也能先顯示上一份成功快照，
  且任一寫入操作完成後**只有受影響的區塊**重畫
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


---

## 區塊級更新（2026-08-03 補，原草案缺這一段）

### 為什麼要補

原草案只解決「進頁面要不要等 API」，沒解決**畫面怎麼更新**。
現況是 `navTo()` → `renderMain()` → `m.innerHTML = ...` **整塊重寫**
（`index.html:832-840`）。就算資料改讀快照，畫面仍整塊重畫：

- 搜尋框打到一半的字沒了
- 捲動位置回到頂端
- 已展開的專利詳情列收合
- 「資料維護」的收合狀態重置

使用者定案：**只在最小範圍內 refresh，不要整頁**。

### 區塊切分

每個頁面切成數個**獨立掛載點**，各自有 id 與自己的重畫函式。
⚠ 判準是「**會不會一起變**」——會一起變的放同一塊，各自變的拆開。

**瀏覽專利**

| 區塊 id | 內容 | 什麼時候重畫 |
|---|---|---|
| `browse-toolbar` | 搜尋框、搜尋鈕 | 幾乎不重畫（重畫就會吃掉使用者打的字） |
| `browse-maintenance` | 文獻備註卡、公司代碼與中文名卡 | 備註任務完成、公司名操作完成 |
| `browse-body` | 專利表格＋分頁列 | 搜尋、翻頁、剔除、公司名變更、備註產生完成 |

**分類區**（2026-08-03 依實際 DOM 修正——原先只寫 4 塊，實際有 8 個掛載點）

| 區塊 id | 內容 | 什麼時候重畫 | 備註 |
|---|---|---|---|
| `topics-header` | 通道分頁、分類鈕、重新挑選、AI 篩不相干 | 只在**任務狀態變化**時（鈕文字要在「分類中…」與「分類」間切換） | ⚠ 不因資料更新而重畫 |
| `topic-candidates` | 分群候選卡 | calibrate／incremental 完成、進入或離開挑選狀態 | 與 `topic-tags` **互斥**（未採用／已採用） |
| `topic-tags` | 主題標籤清單 | rename、merge／unmerge、finalize、AI 標籤完成 | 即原草案的 `topics-list` |
| `exclusion-reviews` | AI 待複核清單 | AI 篩不相干完成、逐筆裁決後 | **有 pending 才出現**；裁決完最後一筆要整區消失 |
| `topic-overview` | 主題概覽 | 同 `topic-tags` | |
| `topic-advanced` | 進階操作（`<details>` 收合） | 幾乎不重畫 | 🔴 **收合狀態必須保住**——重畫就會收起來，而使用者剛展開它是為了操作 |
| `topic-ops` ／ `topic-op-job` ／ `topic-merge-suggestions` ／ `topic-merge-history` | 進階操作的四個子區 | 各自對應的操作完成後 | 位於 `topic-advanced` 內，**分別更新**，不連坐父層 |
| `topic-patents` | 選定主題的專利表 | 剔除、還原、切換主題、finalize | |

⚠ **通道切換（技術／功效）目前會重畫整區**（`index.html:1953-1956`）。
換通道確實換掉整組主題資料，但 `topics-header` 的分頁本身與 `topic-advanced`
的收合狀態沒必要跟著重置——切完通道還要再展開一次進階操作，是多餘的動作。
定案：切通道時重畫 `topic-candidates`／`topic-tags`／`topic-overview`／`topic-patents`，
**不動** `topics-header` 與 `topic-advanced` 的收合狀態。

### 重分群完成後的選取狀態（2026-08-03 使用者定案）

按「分類」重跑分群並完成後，主題清單**整組換掉**（topic_code 由 runner 重編）。
定案：**回到未選狀態**——清掉選定主題、`topic-patents` 收起，只顯示新的主題清單。

⚠ 不做「對回同名主題」：同名不代表同一組專利，會讓使用者以為「還是原來那一群」
而實際成員已經不同——那是比多點一次更貴的錯誤。
⚠ 也不做「停在舊畫面並提示」：舊資料指向的 topic_code 在新一輪已不存在，
繼續顯示等於展示過期內容。

**報表種類**（2026-08-03 納入，🔴 **只做區塊化、不另建快照層**）

⚠ **為什麼與前兩區處理方式不同**：這區的資料來源是**版本化產物**
（`report_trial_YYYYMMDD_HHMMSS`，存 `app_layer.report_artifacts`），不是即時 DB 查詢。

| | 瀏覽專利／分類區 | 報表種類 |
|---|---|---|
| 資料來源 | DB 即時查 | **版本化產物**（已有版本號） |
| 「新舊」怎麼判斷 | 要比對時間戳 | **版本號本身就是** |
| 快照要做什麼 | 產生一份快照檔 | **產物本身已經是快照** |

🔴 **不再產一層快照檔**：產物已在 `report_artifacts`，再產一層是同一份資料的
第二個落點——本專案已因「同一資訊兩處落點」反覆靜默失敗（前後端欄名、report_keys、
workspace_id、表格欄位對照、編碼說明、column_labels）。
本區只做：① 區塊拆分 ② 前端快取「目前選定版本的 content」，換版本才重取。

| 區塊 id | 內容 | 什麼時候重畫 | 備註 |
|---|---|---|---|
| `reports-header` | 檢視選單、市場資料收合區、一次產全部解讀鈕 | 版本清單變動（新版本產出）、任務狀態變化 | ⚠ 選單的**選定值**要保住 |
| `report-generate` | 產製勾選清單（15 種）＋產製鈕 | 任務狀態變化 | `<details>` **收合狀態必須保住** |
| `report-job` | 產製任務進度 | 任務狀態變化 | |
| `report-version-list` | 既有報表版本清單 | report_generate 完成 | |
| `market-side-by-side` | 市場側摘要（只讀已確認現行版） | 市場摘要確認後 | ⚠ 全庫隱藏 |
| `report-inline-view` | 專利側報表卡（圖表＋數據表＋解讀） | 切報表、切通道、切 variant、單張重產解讀完成 | 🔴 切換**不得重抓 API**——同一版本的 content 快取一份即可 |
| `market-upload` | 市場 PDF 上傳區 | 上傳完成、摘要產出 | 位於 `reports-header` 的收合區內，**分別更新** |

**匯出報告**（2026-08-03 納入）

| 區塊 id | 內容 | 什麼時候重畫 | 備註 |
|---|---|---|---|
| `export-toolbar` | 編輯模式開關、版本下拉、四個操作鈕 | 版本清單變動、任務狀態變化 | ⚠ **編輯模式開關與版本選定值要保住** |
| `export-job` | 任務進度 | 任務狀態變化 | |
| `export-ppt-result` | PPT 產出結果與下載連結 | `ai:report_ppt` 完成 | |
| `export-preview` | 整份報告預覽 | 換版本、重新載入、解讀重產完成、**人工編輯儲存後** | 與報表區共用 `renderReportContentHtml` |

⚠ `export-preview` 內容量大（整份報告全部頁面），**換版本以外的操作不得整區重畫**：
單張解讀重產只換那一張卡、人工編輯只換被編輯的那一段。

### 🔴 人工編輯稿落 DB（2026-08-03 使用者定案）

**現況問題**：人工編輯稿存在瀏覽器 `localStorage`
（`patent_export_edits:{version}`，`index.html:3806-3812`）——
換裝置、清快取就遺失，而且**後端完全不知道它存在**。

⚠ 這是全站唯一「只存在前端、後端沒有對應物」的資料。
使用者本輪的核心要求是「避免前後端不同步」，而這份資料連「同步」的對象都沒有。
且人工改過的文案是**交付物**，不該因為換一台電腦就沒了。

**定案：落 DB，納入本階段。**

契約：

| 項目 | 內容 |
|---|---|
| 主鍵 | （報表版本, report_key, variant_key） |
| 欄位 | `manual_text`（人工稿）／`ai_original`（AI 原稿快照）／`updated_at` |
| 分欄原則 | 🔴 **人工稿永不覆蓋 AI 稿**——沿用現有設計，`ai_original` 保留供「還原 AI 原稿」 |
| 寫入時機 | 編輯模式下離開該欄位或按儲存時 |
| 影響區塊 | `export-preview`（只換被編輯那一段） |

⚠ **遷移**：既有 `localStorage` 內容不自動上傳——那是本機資料，
使用者可能在多台機器有各自版本，自動合併會覆蓋掉不該覆蓋的。
改為：偵測到本機有舊資料時提示「這台電腦有未同步的修改，要上傳嗎？」由使用者決定。

### 報表種類的第二階段寫入操作（2026-08-03 起納入本階段）

| 前端函式 | 動作 | 影響區塊 |
|---|---|---|
| `submitReports` | 產製選定報表（長任務） | `report-job`（進行中）→ 完成後 `report-version-list`、`reports-header`（選單）、`report-inline-view` |
| `triggerExport` | 重新產製報表資料（長任務） | 同上 |
| `runNarrative`（不帶 report_keys） | 一次產全部解讀（長任務） | `report-job` → 完成後 `report-inline-view` **全部卡** |
| `runNarrative`（帶 report_keys） | 重產單張解讀 | `report-job` → 完成後**只換那一張卡** |
| `uploadMarketDocument` | 上傳市場 PDF | `market-upload` |
| `submitMarketSummary` | 產／確認市場摘要 | `market-upload`、`market-side-by-side` |
| `resolveMarketWorkspaceId` | 新建 workspace 再上傳 | `market-upload`、workspace 下拉 |

### 匯出報告的寫入操作（2026-08-03 納入本階段）

| 前端函式 | 動作 | 影響區塊 |
|---|---|---|
| `triggerExport` | 重新產製報表資料（長任務） | `export-job` → 完成後 `export-toolbar`（版本下拉）、`export-preview` |
| `requestExportPpt` | 產生 PPT | `export-job` → 完成後 `export-ppt-result` |
| `runNarrativeThenExportPpt` | 無解讀時先跑 narrative 再接 PPT | `export-job`（兩段進度）→ 完成後 `export-preview`、`export-ppt-result` |
| **（新）** 儲存人工編輯 | 人工稿落 DB | `export-preview`（**只換被編輯那一段**） |

⚠ **仍不在本階段**：案件比對（`createComparison`、`saveTarget`、`saveUnderstanding`、
`approveUnderstanding`、`saveElementAnalysis`）—— 入口已於 2026-08-03 移除，
實作保留；恢復入口時再一併納入。

### 操作 → 影響區塊對照表

🔴 這張表就是使用者說的「**所有功能都有其目的地生效的區域**」。
⚠ 每個寫入 API 的回應都要帶 `affected_snapshots[]`，前端據此決定重畫哪幾塊；
**不得由前端自己猜**——猜錯就是前後端不同步，而且不會報錯。

| 前端函式 | 動作 | 影響區塊 |
|---|---|---|
| `markIrrelevant` | 標不相干（剔除） | `topic-patents`、`topic-tags`（件數變）、`topic-overview` |
| `restoreExcluded` | 還原已剔除 | `topic-patents`、`topic-tags`、`topic-overview` |
| `postExclusionDecision` | 剔除覆核（確認／保留） | `exclusion-reviews`、`topic-patents`、`topic-tags` |
| `renameTopic` | 主題重新命名 | `topic-tags`、`topic-ops`、`topic-patents`（標題列） |
| `queueTopicMerge` | 合併主題 | `topic-tags`、`topic-overview`、`topic-merge-history`、`topic-patents` |
| `submitTopicUnmerge` | 取消合併 | `topic-tags`、`topic-overview`、`topic-merge-history`、`topic-patents` |
| `submitFinalizeCandidate` | 分群定案 | `topic-candidates`、`topic-tags`、`topic-overview`、`topic-patents`、`browse-body`（分類欄） |
| `runClassify` | 重新分群（長任務） | `topics-header`（鈕狀態）→ 完成後同 `submitFinalizeCandidate` |
| `requestTopicLabel` | AI 主題名稱 | `topic-label-status`（進行中）→ 完成後 `topic-tags`、`topic-overview` |
| `runIrrelevantFilter` | AI 不相干篩選（長任務） | `topics-header`（鈕狀態）→ 完成後 `exclusion-reviews` |
| `runPatentNotes` | 產文獻備註（長任務） | `browse-maintenance`（覆蓋率）、`browse-body`（備註欄） |
| `confirmCompanyCodes` / `confirmPendingCodeGroup` / `renameCompanyGroup` / `deleteCompanyGroup` / `promoteCompanyCode` / `removeCompanyVariant` / `markNameNotGrouped` / `restoreNotGroupedName` | 公司代碼與中文名維護 | `browse-maintenance`、`browse-body`（申請人／專利權人／受讓人欄） |

⚠ **不在本階段**（報表、匯出、比對、市場文件相關的 11 個寫入函式）：
`submitReports`、`triggerExport`、`runNarrative`、`requestExportPpt`、
`runNarrativeThenExportPpt`、`createComparison`、`saveTarget`、`saveUnderstanding`、
`approveUnderstanding`、`saveElementAnalysis`、`uploadMarketDocument`、
`submitMarketSummary`、`resolveMarketWorkspaceId`。
它們的資料來源是**報表版本產物**（已有版本號），快照邏輯與專利清單不同，第二階段再做。

### 前端契約

```js
// 寫入完成後：後端說哪些區塊髒了，前端就只重畫那幾塊
const res = await postJson(url, body);
for (const snap of res.affected_snapshots || []) {
  await reloadSnapshot(snap);          // 讀新快照
}
refreshBlocks(blocksFor(res.affected_snapshots));   // 只重畫對應區塊
```

⚠ `blocksFor()` 的對照表是**唯一來源**，放前端一處；
不得在每個操作的 callback 裡各寫一次「順便刷新哪裡」——那正是現在
「有些操作忘了刷新」的成因。

### 驗收判準（本節）

1. 在搜尋框輸入文字→按任一維護操作→**輸入的文字還在**
2. 展開某筆專利詳情→剔除另一筆→**展開狀態保留**
3. 專利表捲到第 50 列→產文獻備註完成→**捲動位置不變**，備註欄有值
4. 對照表列出的每一個操作，完成後對應區塊都有更新（可用 Playwright 逐項驗）
5. 對照表**沒有列到**的區塊不得被重畫（避免「保險起見全刷」讓 1–3 失效）

## 其餘開放問題的處置（2026-08-03）

| 原開放問題 | 處置 |
|---|---|
| localStorage vs IndexedDB | **不適用**——已定案走後端檔案快照，前端不做本地持久化 |
| 是否保留最近 N 版 snapshot | 依原草案建議：**只保留 latest**，需要回溯時重產 |
| Nginx 直送 vs FastAPI | 部署期再定；第一版走 FastAPI，介面不變 |
| 背景 freshness check | 依原草案建議：**不做**。只顯示資料時間並提供 refresh |
