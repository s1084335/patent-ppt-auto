# Patent Ingestion Design

## 架構與資料流

Web API 先把上傳內容寫入 DB-backed `import_blobs`，建立 `patent_import` 工作；worker materialize 暫存檔後呼叫 WIPS importer。Importer 依 mapping 正規化欄名與法律狀態，寫入 raw/core 關聯，再由後續工作刷新 derived、補 embeddings 與 workspace 範圍。

Web 白名單不含 `.mdb`；CLI importer 仍保有 `.mdb` 讀取能力。這兩個入口語意不同，不應誤合併成同一白名單。

## 程式落點

- 上傳 API：`backend/app/api/imports.py`
- 路徑與格式防護：`backend/app/importers/import_paths.py`
- 解析與落庫：`backend/app/importers/wips_importer.py`
- 欄位 mapping：`backend/app/mappings/wips.py`、`backend/app/mappings/legal_status.py`
- Blob：`backend/app/db/import_blob_store.py`
- Migration：`alembic/versions/0001_*` 至 `0046_core_field_reclassification.py`

## 測試證據

- `tests/test_api_imports.py`
- `tests/test_import_job.py`
- `tests/test_wips_import_flow.py`
- `tests/test_wips_importer_0019_0021.py`
- `tests/test_wips_importer_empty_update.py`
- `tests/test_wips_patent_numbers.py`
- `tests/test_import_format_fixes.py`
- `tests/test_import_blob_store.py`、`tests/test_import_blobs_migration.py`
- `tests/test_core_field_reclassification.py`
- `tests/test_company_alias_importer.py`

## 輸出契約

匯入摘要至少包含來源格式、來源列數、新增／更新／略過數、受影響 patent IDs、錯誤與後續工作資訊。正式資料輸出分為 raw provenance、核心專利、人員、attributes 與代表圖。

## 已知未完成

CSV/TXT/XML 的串流與解析強化仍屬 active change；0046 在正式 DB 的完整 upgrade、refresh、重匯與報表驗收也尚未形成 baseline 驗收證據。

