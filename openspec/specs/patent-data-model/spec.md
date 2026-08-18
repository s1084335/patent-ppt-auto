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


### Requirement: DAT-008 公司代碼為獨立實體

系統 SHALL 以獨立資料表定義公司代碼的存在，使其他資料可以參照它；
公司代碼 SHALL NOT 只以重複欄位的形式散落在關聯資料中。

#### Scenario: 代碼登記為唯一實體

- **GIVEN** 公司實體表已建立並回填既有代碼
- **WHEN** 系統需要判斷某公司代碼是否存在
- **THEN** SHALL 以公司實體表為唯一依據
- **AND** SHALL NOT 以「別稱表中是否出現過該代碼」推斷

#### Scenario: 臨時代碼的識別由 schema 決定

- **GIVEN** 公司實體表帶有一個衍生欄位標示臨時代碼
- **WHEN** 判斷一個代碼是否為系統產生的臨時代碼
- **THEN** SHALL 由實體表的衍生欄位提供
- **AND** 該欄位 SHALL NOT 可被直接寫入
- **AND** SHALL NOT 由各消費端各自以字串前綴判斷

### Requirement: DAT-009 集團成員對公司代碼的參照完整性

集團成員資料 SHALL 以外鍵約束保證其 `company_code` 指向存在的公司代碼；
指向不存在代碼的孤兒列 SHALL NOT 能夠存在。

⚠ 本 Requirement 只涵蓋集團成員那一條參照。別稱表對公司代碼的外鍵不在範圍內
（2026-08-18 範圍裁決）。

#### Scenario: 仍屬集團的代碼不得刪除

- **GIVEN** 一個公司代碼仍登記為某集團的成員
- **WHEN** 刪除該公司代碼
- **THEN** 系統 SHALL 拒絕該刪除
- **AND** 拒絕 SHALL 由資料庫約束保證，而非由呼叫端自行檢查
- **AND** 使用者 SHALL 先明確移除集團成員關係後才能刪除

#### Scenario: 未屬集團的代碼仍可刪除

- **GIVEN** 一個公司代碼不屬於任何集團
- **WHEN** 刪除該公司代碼
- **THEN** 刪除 SHALL 成功
- **AND** 約束 SHALL NOT 阻擋不涉及集團的刪除

#### Scenario: 代碼變更自動連動集團成員

- **GIVEN** 一個公司代碼登記為某集團的成員
- **WHEN** 該代碼被改寫為另一個代碼
- **THEN** 集團成員關係 SHALL 自動跟隨變更
- **AND** 連動 SHALL 由資料庫保證，不倚賴呼叫端記得逐表更新

#### Scenario: 集團成員必須指向存在的代碼

- **GIVEN** 公司實體表中不存在某個代碼
- **WHEN** 以該代碼建立集團成員關係
- **THEN** 系統 SHALL 拒絕該寫入
