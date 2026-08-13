# ai-companion（delta）

## ADDED Requirements

### Requirement: 建議句須帶依據標記

簡報中的建議句 SHALL 帶 `依據：<出處>` 標記；接不上依據的建議句
SHALL 被閘門擋下。

⚠ 閘門 SHALL 只驗「有無標記」，SHALL NOT 驗依據內容是否充分——
後者屬語意判斷，留給目視迴圈。

#### Scenario: 依據標記檢查

- **WHEN** `check_content` 檢查一頁的建議句
- **THEN** SHALL 以純字串比對確認 `依據：` 存在
- **AND** 缺標記時 SHALL 擋下該句，SHALL NOT 標示為「待驗證」後放行

#### Scenario: 依據標記印在投影片上，流程狀態不印

- **WHEN** 簡報渲染建議句
- **THEN** `依據：CN 121754862 獨立項第 N 要素`、`依據：申請年×主題統計`
  等**證據出處** SHALL 印出
- **AND** `待驗證`、`降級` 等**流程狀態** SHALL NOT 印出

⚠ 分界：證據出處是決策者用得上的資訊；流程狀態是系統的內部判定，
讀者不需要知道。審閱意見第 1 點（降級標示）與第 4 點（產出規則不得上投影片）
互相矛盾，本規格以此分界收斂。

### Requirement: 產出規則不得出現在投影片上

content schema SHALL NOT 提供 `read_me`／`chart_rule` 等承載產出規則的欄位；
閘門 SHALL 以**有限黑名單**擋下已知的指令句字串。

#### Scenario: 結構性移除

- **WHEN** CLI 產生 content
- **THEN** schema SHALL 不存在 `read_me`／`chart_rule` 欄位

#### Scenario: 黑名單閘門

- **WHEN** `check_content` 檢查頁面文字
- **THEN** SHALL 比對有限清單（`本簡報怎麼讀`／`圖表原則`／`待驗證`／`降級`）
- **AND** SHALL NOT 使用模式比對或語意判定

### Requirement: 閘門只收不看語意即可判定的檢查

新增任何閘門前 SHALL 通過判準：**不看語意能不能判定？不能就是建議形。**

⚠ 此護欄源自同一個錯誤踩三次：v5→v6「不得含句號」使 CLI 砍掉數字；
v7→v8「版面用量下限」成為丟棄要點的根因；v9→v10 句型固定被指「只是換皮」。
機制皆為**用形式規則鎖內容，CLI 為了過鎖而犧牲內容**。

#### Scenario: 判讀句品質不進閘門

- **WHEN** 需要提升判讀句深度
- **THEN** SHALL 以建議形＋目視迴圈處理
- **AND** SHALL NOT 加入「每頁至少一句符合三種型別」之類的形式鎖

#### Scenario: 不加數量鎖

- **WHEN** 需要限制口徑說明的篇幅
- **THEN** SHALL 以版型（集中附錄）處理
- **AND** SHALL NOT 加入「正文每頁最多一句口徑說明」之類的數量鎖

### Requirement: 排頁時決定樣本數下限

矩陣／象限頁型 SHALL 於 `plan_deck` 排頁時檢查樣本數；不足 8 筆時
SHALL 改配置為排序表，SHALL NOT 於事後擋下已產出的內容。

#### Scenario: 樣本數不足改頁型

- **WHEN** `plan_deck` 配置矩陣或象限頁且可用樣本 < 8
- **THEN** SHALL 改配置為排序表頁型
