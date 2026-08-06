# Workspace and Browse Design

## 架構與資料流

Workspace 是分群、報表、排除與文件的共同範圍鍵。全庫 workspace 由系統維護，自訂 workspace 保存 patent ID 集合；排除資料另存狀態，不改寫 core patent。列表 projection 從 derived base 取顯示值，原始欄位仍由 core/raw 追溯。

## 程式落點

- API：`backend/app/api/workspaces.py`、`backend/app/api/patents.py`
- Workspace service：`backend/app/clustering/workspace_service.py`
- 查詢：`backend/app/app_layer/workspace_queries.py`
- 排除：`backend/app/clustering/exclusions.py`
- 文件：`backend/app/db/workspace_document_store.py`
- 前端：`backend/app/static/index.html`

## 測試證據

- `tests/test_api_workspaces.py`
- `tests/test_api_workspace_create_and_patents.py`
- `tests/test_api_workspace_queries.py`
- `tests/test_workspace_queries.py`
- `tests/test_global_workspace.py`
- `tests/test_workspace_exclusions.py`
- `tests/test_exclusion_restore.py`
- `tests/test_api_exclusion_reviews.py`
- `tests/test_api_workspace_documents.py`
- `tests/test_api_patents_*.py`
- `tests/test_patent_display_normalized_names.py`

## 輸出契約

API 輸出包括 workspace metadata、專利列表／詳情、排除與待複核狀態、代表圖 bytes、文件 metadata/content。列表欄位與前端欄位需由契約測試鎖定，避免前後端各自維護不同清單。

## 已知未完成

前端 snapshot cache 與所有 SSE 資料區塊掛點尚未完成；真資料的全欄位顯示仍需在對應 active change 做實機驗收。

