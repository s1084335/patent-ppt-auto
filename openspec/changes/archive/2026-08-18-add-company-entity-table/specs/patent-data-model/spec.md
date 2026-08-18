# patent-data-model（delta）

## ADDED Requirements

### Requirement: 公司代碼為獨立實體

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
- **AND** 該欄位 SHALL NOT 可被直接寫入（避免與代碼本身漂開）
- **AND** SHALL NOT 由各消費端各自以字串前綴判斷

### Requirement: 集團成員對公司代碼的參照完整性

集團成員資料 SHALL 以外鍵約束保證其 `company_code` 指向存在的公司代碼；
指向不存在代碼的孤兒列 SHALL NOT 能夠存在。

⚠ 本 Requirement 只涵蓋**集團成員**那一條參照。別稱表對公司代碼的外鍵
不在本次範圍（見 proposal Scope 的 2026-08-18 裁決「丙」）。

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
- **AND** 約束 SHALL NOT 阻擋不涉及集團的刪除（過度攔截與擋不住同樣是缺陷）

#### Scenario: 代碼變更自動連動集團成員

- **GIVEN** 一個公司代碼登記為某集團的成員
- **WHEN** 該代碼被改寫為另一個代碼（例如臨時代碼轉正）
- **THEN** 集團成員關係 SHALL 自動跟隨變更
- **AND** 連動 SHALL 由資料庫保證，不倚賴呼叫端記得逐表更新

#### Scenario: 集團成員必須指向存在的代碼

- **GIVEN** 公司實體表中不存在某個代碼
- **WHEN** 以該代碼建立集團成員關係
- **THEN** 系統 SHALL 拒絕該寫入
