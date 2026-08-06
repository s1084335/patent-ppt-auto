# Patent Data Model Specification

## Purpose

定義專利資料四層架構、欄位所有權、derived projection、版本輸出與 migration 契約。

## Requirements

### Requirement: DAT-001 四層資料責任

系統 SHALL 將來源追溯放在 `raw_layer`、可重用領域資料放在 `core_layer`、可重建分析投影放在 `derived_layer`、工作流程與版本輸出放在 `app_layer`。

#### Scenario: 重新建立 derived 資料

- **GIVEN** raw/core 資料仍完整
- **WHEN** 執行 derived refresh
- **THEN** derived 投影 SHALL 可重新建立
- **AND** 不要求重新匯入原始檔

### Requirement: DAT-002 核心專利欄位所有權

系統 SHALL 以 `core_layer.patents` 保存一件專利的一對一核心欄位，以 `patent_people` 保存可重複人員，以 `patent_sources` 保存來源關聯，以 `patent_attributes` 保存未被正式流程使用的殘餘欄位。

#### Scenario: 同一知識避免重複定義

- **WHEN** 欄位升格為分析或顯示正式欄位
- **THEN** schema、mapping、importer 與 derived projection SHALL 同步遷移
- **AND** attributes 不得繼續作為第二正式來源

### Requirement: DAT-003 報表基礎投影

系統 SHALL 由 derived layer 提供一專利一列的報表基礎投影，以及共同申請人分析專用的申請人展開投影。

#### Scenario: 一般專利件數

- **WHEN** 非申請人展開報表計算件數
- **THEN** SHALL 使用一專利一列的 base
- **AND** 不因共同申請人產生重複件數

### Requirement: DAT-004 Migration 可重現

系統 SHALL 以 Alembic migration 建立、升級與回復 schema，migration 必須有 schema、資料搬移與 downgrade 契約測試。

#### Scenario: Fresh database

- **GIVEN** 一個空白 PostgreSQL database
- **WHEN** 執行 `alembic upgrade head`
- **THEN** SHALL 建立目前應有的 raw/core/derived/app 物件

### Requirement: DAT-005 DB 連線相容

系統 SHALL 支援 `DATABASE_URL` 及 PG 環境變數，並在 pooler 環境停用不相容的 prepared statement 行為。

#### Scenario: 經 PgBouncer 連線

- **WHEN** 應用程式使用支援的 Supabase/PgBouncer 連線
- **THEN** 連線設定 SHALL 避免 `DuplicatePreparedStatement`

### Requirement: DAT-006 欄位重分類資料完整性

系統 SHALL 在欄位由 `patent_attributes` 搬入 core tables 後，保持既有資料、匯入更新、derived projection 與 downgrade 的一致性。

#### Scenario: 升級既有資料庫

- **GIVEN** 目標 DB 含 0046 前的專利與 attributes 資料
- **WHEN** 執行 migration upgrade
- **THEN** 搬移欄位 SHALL 存在於指定 core table
- **AND** 可搬移的既有值 SHALL 被保留
- **AND** dependent views SHALL 可重建

#### Scenario: 重新匯入更新欄位

- **GIVEN** 來源檔含 0046 搬移欄位
- **WHEN** importer 命中既有專利
- **THEN** 欄位 SHALL 以 psycopg 安全參數更新
- **AND** 不因中文欄名括號造成參數錯誤
