## ADDED Requirements

### Requirement: DAT-007 TW 法律狀態目前值與首次登錄歷程

系統 SHALL 在 `core_layer.patents` 保存專利目前的 `legal_status`，並在同一 patent row 的 JSONB 陣列保存法律狀態登錄歷程；系統 MUST NOT 為此建立新 table。每筆歷程 SHALL 僅包含 `from_status`、`to_status`、`changed_at`，且 `changed_at` SHALL 由伺服器產生。

#### Scenario: 首次登錄目前值與歷程
- **GIVEN** 一件 TW 專利的 `legal_status` 為 NULL 或空白
- **WHEN** 系統接受一個合法狀態的首次登錄
- **THEN** `legal_status` SHALL 保存選定狀態
- **AND** `legal_status_history` SHALL append 一筆 `from_status=null`、正確 `to_status` 與伺服器時間

#### Scenario: Migration 保留既有資料且不建立 table
- **GIVEN** 資料庫已有專利資料
- **WHEN** 執行本變更的 Alembic upgrade
- **THEN** 既有欄位與資料 SHALL 保持不變
- **AND** 既有 rows 的 `legal_status_history` SHALL 為非 NULL 空陣列
- **AND** schema SHALL NOT 因本功能新增 table

#### Scenario: Downgrade 回復欄位
- **WHEN** 執行本變更的 Alembic downgrade
- **THEN** 系統 SHALL 移除 `legal_status_history` 欄位
- **AND** SHALL NOT 修改既有 `legal_status` 值或其他專利欄位
