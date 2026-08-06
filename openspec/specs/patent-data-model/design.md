# Patent Data Model Design

## 架構

- `raw_layer`：來源檔、raw records 與匯入 blob。
- `core_layer`：patents、patent_sources、patent_people、patent_attributes、分類、embedding、圖像。
- `derived_layer`：報表 base、申請人展開 view、家族投影、公司別名與待處理 projection。
- `app_layer`：workspace、workflow runs/outputs、topic state、report artifacts、comparison state。

核心欄位清單的唯一來源由 mapping/schema/importer 契約共同鎖定；derived 僅消費，不重新定義欄位語意。

## 程式落點

- Migration：`alembic/versions/`
- DB 連線：`backend/app/db/connection.py`
- Derived refresh：`backend/app/derived/`
- App repositories：`backend/app/app_layer/`、`backend/app/repositories/`
- Schema comments：`backend/app/db/schema_comments.py`

## 測試證據

- `tests/test_migration_contract.py`
- `tests/test_schema_comments.py`
- `tests/test_db_isolation_guard.py`
- `tests/test_pool_prepare_threshold.py`
- `tests/test_refresh_derived_scope_integrated.py`
- `tests/test_refresh_alias_guard.py`
- `tests/test_derived_classification_columns.py`
- `tests/test_expanded_view_aggregates.py`
- 各 migration 專屬測試：`tests/test_*migration*.py`

## 輸出契約

正式 schema 由 migration head 定義；文件不得硬寫舊表數或 migration 版本當永久契約。需要宣告 live DB 狀態時，必須實際查詢 `alembic_version`、目標欄位及資料填充，不得只引用本規格。

## 風險

View 相依順序、欄位重分類與正式 DB 資料搬移是高風險區。升級前需備份或使用拋棄式副本；完整驗收至少涵蓋 upgrade、derived refresh、關鍵非空值與受影響報表 smoke。

