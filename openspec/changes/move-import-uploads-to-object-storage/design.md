# Design: 匯入上傳移至物件儲存

## Context

現行 `imports.py -> import_blob_store -> job(blob_id) -> handlers.py` 以 PostgreSQL bytea 跨 backend/worker 傳檔。終結態刪除、orphan dry-run 與分塊讀寫已存在，但大檔仍必須先完整寫進 DB。部署環境的兩個容器沒有共享檔案系統，不能退回本機 path。

## Goals / Non-Goals

**Goals:** S3 相容、串流、hash 可驗、重試安全、終結態清理、舊 job 相容、provider 可替換。

**Non-Goals:** 本輪不搬正式長生命週期 bytea；不改 importer 與 dedupe；不提供公開 object URL。

## Decisions

### 1. Port/adapter 是唯一 object-store 定義處

建立最小介面 `put_stream`、`download_to_path`、`delete`、`list_orphans/head`。業務層只依賴介面；R2/S3/MinIO 都由 endpoint/config 決定。相較直接散落 SDK 呼叫，此作法才能以 fake adapter 做 Red/Green 並避免 provider lock-in。

### 2. Job payload 保存 opaque key 與完整性 metadata

payload 使用 `object_key`、`file_hash`、`byte_size`、`original_filename`；不得放 presigned URL/secret。key 由 server 產生並限制 prefix。Worker 下載到既有受控 temporary directory，hash 通過後才呼叫 importer。

### 3. 雙讀、單寫切換

過渡版 reader 接受 `object_key` 或 `blob_id`，writer 由 feature flag 決定且每個 request 只能選一條。先啟 object writer，等 queued/running 舊 jobs 歸零後再 drop table；不做雙寫，避免兩份內容生命週期漂移。

### 4. Cleanup 綁 terminal transition，但失敗可補償

可重試狀態不刪；terminal transition 記 cleanup outcome。業務提交成功而 delete 暫時失敗時，job 結果保留成功並標 cleanup pending，由週期性 dry-run/execute 補刪，避免重跑 importer。

## Code And Data Boundaries

- API：`backend/app/api/imports.py`
- adapter/config：`backend/app/storage/` 或既有 db store 同層的明確 port
- worker/job lifecycle：`backend/app/worker/handlers.py`、`backend/app/db/job_repository.py`
- migration：先相容 payload；最後確認引用歸零才 drop `app_layer.import_blobs`

## Output And Test Evidence

- API/job summary 保留原匯入統計，另有 storage mode/key prefix/hash/cleanup status（不含 secret）。
- 單元：key、設定、串流、hash、冪等刪除。
- 整合：fake S3/local MinIO、API→worker、retry/terminal/orphan、舊 blob job。
- DB/容量：50 MB+ 檔匯入期間確認 `import_blobs` 不成長，DB/storage 前後 inventory 對帳。

## Risks / Trade-offs

- [外部服務不可用] → 上傳前 health/preflight、清楚 503，不回退半套 DB。
- [刪太早讓 retry 無檔] → 只在 final terminal 刪，狀態決策表測試。
- [migration 遺留 job] → drop 前 SQL gate 驗 queued/running `blob_id` 為零。
- [credential 洩漏] → config redaction、log contract test、payload 禁止 URL/secret。

## Migration Plan

1. adapter + fake tests；2. reader 雙路徑；3. object writer feature flag；4. 測試環境大檔；5. 目標環境選 provider 並切換；6. 觀察／清 orphan；7. 使用者核准後 drop table。Rollback 在第 7 步前切回 blob writer；drop 後 rollback 須先還原 migration，不能無聲降級。
