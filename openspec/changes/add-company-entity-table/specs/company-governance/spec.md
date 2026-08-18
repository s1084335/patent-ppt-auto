# company-governance（delta）

## MODIFIED Requirements

### Requirement: 公司代碼的刪除與轉正

刪除或轉正公司代碼時，系統 SHALL 保證所有參照該代碼的資料一致；
呼叫端 SHALL NOT 需要自行記得更新哪些關聯資料表。

#### Scenario: 刪除被集團引用的代碼

- **GIVEN** 一個公司代碼仍登記為某集團的成員
- **WHEN** 使用者刪除該公司代碼
- **THEN** 系統 SHALL 回應衝突狀態並說明原因
- **AND** 訊息 SHALL 指出該代碼卡在集團與下一步操作
- **AND** SHALL NOT 回應無法理解的資料庫層錯誤
- **AND** 判斷 SHALL 由資料庫約束作出，SHALL NOT 改以「先查詢再刪除」替代
  （有競態，且新增端點會漏掉）

#### Scenario: 刪除未被集團引用的代碼

- **GIVEN** 一個公司代碼不屬於任何集團
- **WHEN** 使用者刪除該公司代碼
- **THEN** 刪除 SHALL 成功並照常排入 derived refresh
- **AND** 系統 SHALL NOT 因新增的約束而阻擋此類刪除

#### Scenario: 臨時代碼轉正

- **GIVEN** 一個臨時代碼已登記且為某集團的成員
- **WHEN** 使用者將它換成來源查得的正式代碼
- **THEN** 該代碼的別稱與集團成員關係 SHALL 一併變更
- **AND** 集團成員的變更 SHALL 由資料庫連動保證，不倚賴呼叫端逐表更新
- **AND** 集團統計 SHALL NOT 因轉正而遺漏該公司

#### Scenario: 轉正目標已存在

- **GIVEN** 使用者填入的目標代碼已經存在於系統
- **WHEN** 執行轉正
- **THEN** 系統 SHALL 拒絕並說明這是合併而非轉正
- **AND** SHALL 指引使用者改走合併流程

### Requirement: 新增公司代碼的單一登記處

任何路徑建立集團成員關係時 SHALL 先於公司實體表登記該代碼；
集團成員 SHALL NOT 指向未登記的代碼。

⚠ 別稱表**不受**此約束保護（本次未加該條外鍵，見 proposal Scope）。
正常寫入路徑會一併登記，但直接以 SQL 寫入別稱不會被擋。

#### Scenario: AI 建議確認產生新代碼

- **GIVEN** 一筆 AI 建議需要建立系統中尚未存在的公司代碼
- **WHEN** 使用者確認該建議
- **THEN** 系統 SHALL 先登記該代碼於公司實體表
- **AND** 再寫入其別稱與集團成員關係

#### Scenario: 未登記的代碼不得建立集團成員

- **GIVEN** 某個代碼未登記於公司實體表
- **WHEN** 任何路徑試圖以該代碼寫入集團成員
- **THEN** 系統 SHALL 拒絕該寫入
