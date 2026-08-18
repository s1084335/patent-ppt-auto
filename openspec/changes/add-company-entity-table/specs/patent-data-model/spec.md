# patent-data-model（delta）

## ADDED Requirements

### Requirement: 公司代碼為獨立實體

系統 SHALL 以獨立資料表定義公司代碼的存在，使其他資料可以參照它；
公司代碼 SHALL NOT 只以重複欄位的形式散落在關聯資料中。

#### Scenario: 代碼登記為唯一實體

- **WHEN** 系統需要判斷某公司代碼是否存在
- **THEN** SHALL 以公司實體表為唯一依據
- **AND** SHALL NOT 以「別稱表中是否出現過該代碼」推斷

#### Scenario: 臨時代碼的識別由 schema 決定

- **WHEN** 判斷一個代碼是否為系統產生的臨時代碼
- **THEN** SHALL 由實體表的衍生欄位提供
- **AND** SHALL NOT 由各消費端各自以字串前綴判斷

### Requirement: 公司代碼的參照完整性

參照公司代碼的資料 SHALL 以外鍵約束保證其指向存在的代碼；孤兒列
SHALL NOT 能夠存在。

#### Scenario: 別稱隨公司刪除而移除

- **WHEN** 刪除一個公司代碼
- **THEN** 該代碼的所有別稱 SHALL 一併移除
- **AND** 別稱 SHALL NOT 留下指向不存在代碼的列

#### Scenario: 仍屬集團的代碼不得刪除

- **WHEN** 刪除一個仍登記為集團成員的公司代碼
- **THEN** 系統 SHALL 拒絕該刪除
- **AND** 拒絕 SHALL 由資料庫約束保證
- **AND** 使用者 SHALL 先明確移除集團成員關係後才能刪除

#### Scenario: 代碼變更自動連動

- **WHEN** 一個公司代碼被改寫為另一個代碼
- **THEN** 該代碼的所有別稱與集團成員關係 SHALL 自動跟隨變更
- **AND** 連動 SHALL 由資料庫保證，不倚賴呼叫端逐表更新

#### Scenario: 集團成員必須指向存在的代碼

- **WHEN** 建立集團成員關係
- **AND** 指定的公司代碼不存在於公司實體表
- **THEN** 系統 SHALL 拒絕該寫入
