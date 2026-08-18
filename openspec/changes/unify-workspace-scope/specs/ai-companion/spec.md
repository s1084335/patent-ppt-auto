# ai-companion（delta）

## MODIFIED Requirements

### Requirement: 取證查詢的 workspace 範圍由單一機制保證

當一次 AI 任務綁定 workspace 時，系統 SHALL 以單一機制限制其即時 DB 取證範圍；
SHALL NOT 存在兩套並行、各自讀不同設定來源的範圍限制。

⚠ 兩套並行時，一次只有一套生效取決於呼叫端設了哪個變數——這種「靠呼叫端不重疊」
的安全性不會在出錯時報錯。

#### Scenario: 只有一個範圍綁定入口

- **GIVEN** 系統同時服務簡報與報表解讀兩條 AI 線
- **WHEN** 任一條線要把任務綁定到某個 workspace
- **THEN** 兩條線 SHALL 使用同一個綁定入口與同一個設定來源

#### Scenario: 未綁定時維持一般查詢行為

- **GIVEN** 一次 AI 任務沒有綁定 workspace
- **WHEN** 執行即時 DB 取證
- **THEN** 查詢行為 SHALL 與未引入範圍限制前相同

### Requirement: 範圍限制在彙總之前生效

綁定 workspace 後，系統 SHALL 在查詢執行時就限制其可見的專利集合；
SHALL NOT 以「執行後過濾回傳列」作為範圍限制手段。

⚠ 執行後過濾只能砍列，擋不住 `COUNT`／`SUM`／`GROUP BY`——那些數字會以全庫算出，
過濾後仍是錯的，而且看起來完全正常。

#### Scenario: 查 patent 級資料表必須引用範圍

- **GIVEN** 一次任務已綁定 workspace
- **WHEN** 查詢引用 patent 級資料表卻沒有引用系統注入的範圍集合
- **THEN** 系統 SHALL 拒絕該查詢
- **AND** 錯誤訊息 SHALL 說明應如何改寫（可直接引用的範圍集合名稱與欄位）
- **AND** SHALL NOT 靜默地只回傳其中一部分列

#### Scenario: 彙總查詢在範圍內可用

- **GIVEN** 一次任務已綁定 workspace
- **WHEN** 查詢以範圍集合限制後進行彙總
- **THEN** 查詢 SHALL 被允許執行
- **AND** 彙總結果 SHALL 只涵蓋該 workspace 的專利
- **AND** 系統 SHALL NOT 僅因為它是彙總就拒絕

#### Scenario: 空 workspace 不得退回全庫

- **GIVEN** 綁定的 workspace 沒有任何成員專利
- **WHEN** 執行 scoped 取證查詢
- **THEN** 系統 SHALL 拒絕該查詢並說明範圍是空的
- **AND** SHALL NOT 靜默地以全庫作答

### Requirement: 提示文件與實際機制一致

給 CLI 的取證指引 SHALL 描述目前實際生效的機制；
SHALL NOT 保留已被機制取代的自律型限制。

#### Scenario: 指引描述彙總的正確做法

- **GIVEN** 範圍限制已由查詢層機制保證
- **WHEN** CLI 讀取取證指引
- **THEN** 指引 SHALL 說明彙總需引用範圍集合
- **AND** SHALL NOT 敘述「綁定 workspace 時不得使用彙總」
