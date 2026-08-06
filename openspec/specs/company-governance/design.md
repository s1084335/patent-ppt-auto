# Company Governance Design

## 架構與資料流

確定性代碼歸戶與 AI 中文名是兩條不同資料流。代碼歸戶由 importer/治理函式處理；AI 只寫 `ai_suggested` 草稿，使用者確認後才改正式欄。Derived refresh 依單一 COALESCE 優先序產出報表與 UI 共用顯示名。

## 程式落點

- 維護 API：`backend/app/api/company_aliases.py`
- 匯入治理：`backend/app/derived/company_alias_importer.py`
- 公司正規化：`backend/app/transforms/company_normalization.py`
- Derived refresh：`backend/app/derived/refresh_report_base.py`
- AI runner：`backend/app/worker/ai_company_zh_name_runner.py`

## 測試證據

- `tests/test_applicant_code_auto_group.py`
- `tests/test_applicant_code_convergence.py`
- `tests/test_company_alias_importer.py`
- `tests/test_company_code_registry.py`
- `tests/test_company_group_maintenance.py`
- `tests/test_no_code_name_matching.py`
- `tests/test_ai_company_zh_name.py`、`tests/test_ai_company_zh_name_db.py`
- `tests/test_company_zh_name_review.py`
- `tests/test_zh_drafts_columns.py`
- `tests/test_zh_name_display_priority.py`

## 輸出契約

公司治理輸出包括群組、alias variant、review status、confirmed 中文名與待處理清單；AI 原始建議與人工正式值分欄保存。報表與 UI 只消費 confirmed／deterministic projection。

## 風險與限制

名稱相似不等於同一法人，因此不以 fuzzy matching 自動寫正式群組。刪除或 promote 會影響大量 derived 顯示，必須以範圍 refresh 與顯示回歸驗收。

