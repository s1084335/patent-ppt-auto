# Retention And Archive Plan

> Legacy source：本檔只保留 volume、NAS、還原與資料類別的細部規劃。權威變更規格、設計與任務位於 `openspec/changes/implement-retention-archive/`；兩者衝突時以 OpenSpec 為準。

## Summary Card

- 更新：2026-07-30
- 狀態：規劃草案，尚未實作
- 已定案：功能不變下先瘦暫存、輸出、job history、artifact；raw/core 不先清；舊報表/PPT/圖表採保守長保留
- 現況：目前 DB 在 Supabase，backend / worker 在 Lightning；現有 `-v` 只有 `/app/data` 與 `/app/output` 的持久化與共享，尚未等同正式 NAS 備份/清理
- 下一驗收點：定案 retention 參數後，先實作低風險清理：`import_blobs` 排程與輸出 metadata 規劃
- 詳情：本文件定義 DB 第三/第四層與 volume 檔案的封存、清理、備份邊界；互動式待決問題另於 Codex 對話中提出

## 目的

本規劃回答三件事：

1. 目前 `-v`、DB 第三層與第四層哪些資料可以瘦身。
2. 正式版如何把「檔案類」放 NAS，把「索引、狀態、追溯」放 DB。
3. 在功能不變的前提下，建立保守的封存與清理策略，避免有人很久後要回頭對照時資料已被刪除。

本文件是規劃，不授權立即刪除資料或修改 production 資料。

## 現況

Lightning / `docker run` 主線目前使用兩個 shared persistent volumes：

```text
patent-data   -> /app/data
patent-output -> /app/output
```

目前用途：

```text
/app/data
  PatentSBERTa model
  HF cache
  clustering model artifact

/app/output
  report output
  charts
  PPT
  report_data.json
  future snapshots
```

目前已有的清理機制：

- `app_layer.import_blobs`：job 終結時嘗試清除對應 blob；另有 orphan cleanup CLI，但尚未確認已排程。
- `workflow_runs` stale recovery：worker 每輪回收超過 `WORKER_STALE_AFTER_SECONDS` 的 running job，預設 1800 秒。
- AI payload 暫存：部分 AI runner 會清除 7 天前的 `var/ai_payloads` JSON。

目前沒有完整機制：

- `/app/data` clustering artifact 定期清理。
- `/app/output` 報表、PPT、圖表、snapshot 定期清理。
- `workflow_runs` / `workflow_outputs` 歷史壓縮或封存。
- `report_artifacts` 大型內容搬移到 NAS。
- DB 備份與 volume/NAS 檔案的一致還原流程。

## 分層原則

正式版採用以下邊界：

```text
檔案本體 -> NAS / shared storage
結構化資料、索引、狀態、追溯 -> DB
```

DB 第三層 `derived_layer`：

```text
可重算、可查詢、可被前端/報表/worker 重複使用的衍生分析資料。
```

DB 第四層 `app_layer`：

```text
使用者操作、workspace、job queue、job output、檔案 metadata、應用運作狀態。
```

新增 retention / archive 相關 metadata 時，優先放 `app_layer`。原因是封存與清理屬於應用生命週期管理，不是專利分析結果本體。

## Volume 與 NAS 策略

正式版程式只認容器內路徑，不認公司實體路徑：

```text
MODEL_ARTIFACT_ROOT=/app/data/model_artifacts
SNAPSHOT_ROOT=/app/output/snapshots
OUTPUT_ROOT=/app/output
```

公司伺服器部署才決定 host path：

```text
/app/output -> NAS path
/app/data   -> local persistent disk 或 NAS path
```

建議：

- `/app/output` 正式版走 NAS，因為 backend / worker 必須共用輸出、報表、PPT、圖表、snapshot。
- `/app/data/model_artifacts` 可先走本機 persistent disk，另備份到 NAS；若 backend / worker 分機部署且都需要 artifact，再改走 NAS 或 artifact store。
- PostgreSQL data directory 不建議直接放一般 NAS；DB 應使用公司 DB server 自己的磁碟，再備份到 NAS。

## Retention Class

正式版所有可清理資料都應分類：

| retention_class | 語意 | 預設處理 |
|---|---|---|
| `temporary` | 暫存、可重建、失敗中間檔 | 短期清理 |
| `working` | 一般成功產物，使用者可能回看 | 中期保留 |
| `reference` | 下載過、被報表引用、曾交付 | 長期保留 |
| `permanent` | 使用者釘選、正式匯出、法務/管理要求保存 | 不自動刪 |

每筆 artifact metadata 至少應能表示：

```text
retention_class
is_pinned
created_at
last_accessed_at
last_downloaded_at
download_count
expires_at
file_path
file_hash
file_size_bytes
storage_deleted_at
```

## 建議保留策略

### 報表 / PPT / 圖表

採保守策略，避免很久後需要對照時找不到：

| 類型 | 保留策略 |
|---|---|
| 使用者釘選版本 | 永久保留，除非人工刪除 |
| 正式匯出版本 | 永久保留，除非人工刪除 |
| 使用者下載過的版本 | 至少 1 年 |
| 一般成功報表版本 | 180 天 |
| 每個 workspace + report type 最新版本 | 至少最近 10 版，不受 180 天限制 |
| failed / cancelled job 產物 | 30 天 |
| runner 暫存圖表、中間 JSON | 30 天 |

清理規則不得只看 NAS 檔案時間，必須看 DB metadata：

```sql
WHERE is_pinned = false
  AND retention_class <> 'permanent'
  AND expires_at < now()
  AND storage_deleted_at IS NULL
```

### Snapshot

snapshot 與正式報表分開處理。

建議預設：

| snapshot 類型 | 保留策略 |
|---|---|
| current snapshot | 不刪 |
| 每個 snapshot key 最近版本 | 保留最近 3 到 5 版 |
| `browse_patents` / `workspace_patents` | 7 天 |
| `topic_patents` | 7 天 |
| `classification_topics` | 30 天 |
| 被報表或正式輸出引用的 snapshot | 跟引用方 retention 走 |

snapshot 本體放 NAS：

```text
/app/output/snapshots/...
```

DB 放 manifest：

```text
app_layer.snapshot_manifests
```

### Job History

`app_layer.workflow_runs` 與 `app_layer.workflow_outputs` 不應直接全刪，因為會影響追溯與問題診斷。

建議：

| 類型 | 保留策略 |
|---|---|
| `queued` / `running` | 不清 |
| 最近 terminal jobs | 90 天完整保留 |
| `failed` jobs | 180 天完整保留 |
| `succeeded` jobs | 90 天後可壓縮大型 output |
| 被 artifact / report / snapshot 引用的 job | 不刪，只可壓縮大內容 |

壓縮做法：

```text
大型 result_json -> NAS JSON file
DB 保留 output_type、file_path、file_hash、summary_json
```

### Import Blobs

`app_layer.import_blobs` 是暫存，不是正式保存層。

建議：

```text
terminal import job 後刪除對應 blob
orphan blob 超過 24 小時刪除
每日排程 cleanup_orphan_blobs
```

這是最低風險、最適合先自動化的清理項。

### AI Payload Temp Files

保留現有 7 天策略：

```text
var/ai_payloads/**/*.json
超過 7 天刪除
```

這類檔案是 prompt/CLI 暫存，不是正式輸出。

### Clustering Artifact

`/app/data/model_artifacts` 內的正式模型權重不清。

clustering run artifact 可清，但必須有 reference tracking：

| 類型 | 保留策略 |
|---|---|
| latest completed run artifact | 不刪 |
| 每個 workspace + source_field 最近 completed artifact | 保留最近 3 到 5 版 |
| 被 report / snapshot / job output 引用的 artifact | 不刪 |
| 失敗 run 的 artifact | 30 天 |
| 未被引用且超過保留期的舊 artifact | 先封存，再刪除 |

第一版不建議先自動刪 clustering artifact，因為它會影響 incremental clustering 與歷史對照。

## 不先瘦身的資料

以下不納入第一階段清理：

```text
raw_layer.source_files
raw_layer.raw_records
core_layer.patents
core_layer.patent_sources
core_layer.patent_people
core_layer.patent_attributes
core_layer.patent_embeddings
```

原因：

- raw/core 是追溯與重算基礎。
- 現階段資料量尚未到需要犧牲追溯能力。
- embeddings 雖大，但對分類重用很重要，先靠版本與 hash 重用，不先刪。

## 清理流程

所有正式清理都採兩階段：

```text
1. DB soft delete / mark planned deletion
2. 刪除 NAS / volume 檔案
3. 回寫 storage_deleted_at
```

不允許直接掃 NAS 目錄刪檔，因為 NAS 不知道 DB 是否還引用該檔案。

建議流程：

```text
retention job
→ 查 DB 找出 eligible artifacts
→ 寫入 deleted_at 或 planned_delete_at
→ 刪 NAS file
→ 驗 hash/path
→ 回寫 storage_deleted_at
→ 記錄 cleanup run summary
```

## 備份與還原

正式備份必須分成兩類：

```text
DB backup
  pg_dump / managed DB backup

File backup
  NAS snapshot / backup policy
  覆蓋 /app/output
  覆蓋必要的 /app/data/model_artifacts 或 artifact
```

一致性要求：

- DB 內的 `file_path` 必須能在 NAS 備份中找到。
- DB 內的 `file_hash` 必須能驗證檔案本體。
- 還原演練不能只還原 DB；至少要抽測報表、PPT、snapshot、clustering artifact 是否可讀。

## 實作順序

1. 排程 `import_blobs` orphan cleanup，先以 dry-run 記錄結果。
2. 為正式輸出定義 metadata 欄位與 retention class。
3. 將新 snapshot 設計成 NAS file + DB manifest。
4. 新報表/PPT/圖表輸出改為 NAS file + DB metadata；舊 `report_artifacts` 先保留相容。
5. 建立 retention dry-run API / CLI，只列出將清理項目，不刪。
6. 建立正式 cleanup job，支援 soft delete、檔案刪除、summary output。
7. 補 Playwright / API 驗收：舊版本仍可查、被釘選不刪、過期暫存會清。

## 待決事項紀錄

以下事項尚未由使用者正式定案。後續若環境支援互動式問題選單，應在 Codex 對話中直接提出，不要求使用者回到本文件挑選。

- `/app/output` 正式版是否一律接 NAS。
- `/app/data` 正式版採本機 persistent disk、NAS，或模型本機 / artifact NAS 的混合模式。
- 舊報表 / PPT / 圖表的一般成功版本保留 180 天、365 天，或只清暫存與失敗產物。
- 使用者下載過的版本至少保留 1 年或永久保留。
- 每個 workspace + report type 最少保留最近 5 / 10 / 20 版。
- snapshot 保留 current + 最近 3 / 5 / 10 版。
- workflow history 壓縮時機與 failed job 完整保留期間。
- cleanup 是否先 dry-run 14 天或 30 天。
- 是否允許使用者手動釘選版本避免自動清理。

## 待補實作文件

定案後需要補：

- DB migration spec：artifact metadata、snapshot manifest、retention class 欄位。
- NAS path spec：正式公司伺服器掛載與權限。
- Cleanup runbook：dry-run、正式清理、還原演練。
- Backup restore checklist：DB + NAS 成對還原。
