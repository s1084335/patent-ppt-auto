# company-governance（delta）

## MODIFIED Requirements

### Requirement: 公司代碼的刪除與轉正

刪除或轉正公司代碼時，系統 SHALL 保證所有參照該代碼的資料一致；
呼叫端 SHALL NOT 需要自行記得更新哪些關聯資料表。

#### Scenario: 刪除被集團引用的代碼

- **WHEN** 使用者刪除一個仍是集團成員的公司代碼
- **THEN** 系統 SHALL 回應衝突狀態並說明原因
- **AND** 訊息 SHALL 指出該代碼所屬集團與下一步操作
- **AND** SHALL NOT 回應無法理解的資料庫層錯誤

#### Scenario: 臨時代碼轉正

- **WHEN** 使用者將臨時代碼換成來源查得的正式代碼
- **THEN** 該代碼的別稱與集團成員關係 SHALL 一併變更
- **AND** 集團統計 SHALL NOT 因轉正而遺漏該公司

#### Scenario: 轉正目標已存在

- **WHEN** 轉正的目標代碼已經存在
- **THEN** 系統 SHALL 拒絕並說明這是合併而非轉正
- **AND** SHALL 指引使用者改走合併流程

### Requirement: 新增公司代碼的單一登記處

任何路徑產生新的公司代碼時 SHALL 先於公司實體表登記；
別稱、集團成員等關聯資料 SHALL 在代碼登記後才能建立。

#### Scenario: AI 建議確認產生新代碼

- **WHEN** 使用者確認一筆需要新建代碼的 AI 建議
- **THEN** 系統 SHALL 先登記該代碼
- **AND** 再寫入其別稱

#### Scenario: 未登記的代碼不得建立關聯資料

- **WHEN** 任何路徑試圖為未登記的代碼寫入別稱或集團成員
- **THEN** 系統 SHALL 拒絕該寫入
