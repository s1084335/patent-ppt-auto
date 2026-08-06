## ADDED Requirements

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

