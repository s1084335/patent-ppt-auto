## Why

大型匯入檔目前會先分塊寫入 PostgreSQL `app_layer.import_blobs.content`，即使終結態與孤兒清理已完成，53 MB 等級檔案在「事中寫入」仍會造成 DB I/O、gateway timeout 與備份負擔。需要把跨 backend/worker 的大檔傳遞移到 S3 相容物件儲存，DB 只保存可追溯指標。

## What Changes

- 建立 provider-neutral 的 S3 相容 object store 介面與設定驗證，可由 R2、S3 或內網 MinIO 實作。
- 上傳 API 串流寫入 object store、同步計算 hash 與大小，job payload 改帶不可猜測的 `object_key`。
- worker 串流下載到受控暫存檔、驗證 hash，再執行既有 importer。
- queued/running/retry 保留物件；succeeded、final failed、cancelled 後冪等刪除，另有 dry-run orphan cleanup。
- 先支援 `blob_id`／`object_key` 雙讀，確認無舊 job 後才以 migration 移除 `import_blobs`。

## Capabilities

### New Capabilities

- `object-storage`: 定義大型暫存物件的 key、完整性、生命週期、供應商抽象與稽核契約。

### Modified Capabilities

- `patent-ingestion`: 匯入上傳與 worker 交接由 DB bytea 改為 object key，維持格式、hash、大小與結果契約。
- `platform-runtime`: job 終結、重試與 cleanup 必須共同管理外部物件生命週期。

## Scope

只處理 `import_blobs` 的匯入暫存檔；`report_artifacts`、`workspace_documents`、`patent_figures` 與 `patents.主附圖` 不在本 change。

## Non-goals

- 不在規格中綁定單一雲端供應商。
- 不改 WIPS mapping、identifier merge truth 或 importer 業務規則。
- 不以共享本機路徑取代跨容器 object store。

## Impact

- 程式：`backend/app/api/imports.py`、`worker/handlers.py`、job repository、設定與新 object store adapter。
- DB：job request payload 相容、`import_blobs` 過渡與最終 drop migration。
- 部署：新增 endpoint、bucket、credential、TLS 與 lifecycle 設定；secret 不進 repo/log。
- 測試：fake S3/local MinIO、API/worker/job lifecycle、migration 與大檔串流。

## Activation

程式先以 object-store feature flag／完整設定啟用；未設定時不得半途寫入。正式切換前由使用者指定目標環境的 R2/S3/MinIO endpoint、bucket 與 secret 管理方式。

## Acceptance Gate

使用代表性小檔與 50 MB 以上檔案跑完整匯入，確認 DB 不再承載檔案內容、hash 一致、重試可用、所有終結態無孤兒且舊 `blob_id` job 可完成；使用者明確驗收前不得 drop `import_blobs` 或 archive。
