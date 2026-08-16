# ai-companion（delta）

## ADDED Requirements

### Requirement: 外觀策略敘事使用報表 evidence

AI companion SHALL 使用 `design_protection_detail`、`design_protection_strategy_table` 與 `design_tech_intersections` 作為外觀策略敘事 evidence。

#### Scenario: 敘述外觀保護策略

- **WHEN** content plan 包含外觀保護策略資料
- **THEN** AI companion SHALL 說明申請人採「只走外觀」或「技術+外觀」的可觀察模式
- **AND** SHALL 引用代表案、技術標籤或 evidence 摘要

#### Scenario: 禁止外部 PDF/WIPS 補證

- **WHEN** AI companion 產生外觀策略敘事
- **THEN** SHALL NOT 產生 WIPS 連結
- **AND** SHALL NOT 產生 PDF 連結
- **AND** SHALL NOT 將外觀/技術交叉解讀為侵權、FTO 或法律確定性結論

### Requirement: 建議句須帶依據標記

簡報中的建議句 SHALL 帶 `依據：<出處>` 標記；接不上依據的建議句
SHALL 被閘門擋下。

⚠ 閘門 SHALL 只驗「有無標記」，SHALL NOT 驗依據內容是否充分——
後者屬語意判斷，留給目視迴圈。
閘門 MAY 以有限清單擋下已知空泛依據例句與內部欄位 key 外洩；不得擴大成
廣泛語意評分。

#### Scenario: 依據標記檢查

- **WHEN** `check_content` 檢查一頁的建議句
- **THEN** SHALL 以純字串比對確認 `依據：` 存在
- **AND** 缺標記時 SHALL 擋下該句，SHALL NOT 標示為「待驗證」後放行
- **AND** SHALL 擋下 `依據：整體統計`、`依據：資料分析`、`依據：報表結果`、`依據：趨勢觀察`、`依據：專利資料`、`依據：AI 判斷` 等有限空泛依據例句
- **AND** SHALL 擋下 `family_country_layout` 等內部欄位 key，投影片 SHALL 使用「家族國家布局」等中文顯示名稱

#### Scenario: 依據標記印在投影片上，流程狀態不印

- **WHEN** 簡報渲染建議句
- **THEN** `依據：CN 121754862 獨立項第 N 要素`、`依據：申請年×主題統計`
  等**證據出處** SHALL 印出
- **AND** `依據：整體統計`、`依據：資料分析`、`依據：AI 判斷` 等空泛依據 SHALL NOT 印出
- **AND** `family_country_layout` 等內部欄位 key SHALL NOT 印出
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

### Requirement: 新增閘門須通過三問判準

新增任何閘門前 SHALL 依序通過三問（design §1.2）：

1. **不看語意能不能判定？** 不能 SHALL 改建議形。
2. **滿足它的唯一途徑是不是把事情做對？** 是（恆等式）SHALL 視為安全。
3. **若有自由度，偏差是多出來的還是缺席的？** 缺席 SHALL 改建議形。

⚠ 只問第 1 題不足：「正文每頁最多一句口徑說明」數句子就能判定、不看語意，
但偏差是「刪掉必要的那句」＝缺席，目視兜不住。

⚠ 此護欄源自同一個錯誤踩三次：v5→v6「不得含句號」使 CLI 砍掉數字；
v7→v8「版面用量下限」成為丟棄要點的根因；v9→v10 句型固定被指「只是換皮」。
機制皆為**閘門成為生成者的目標函數，而它只是代理指標**——滿足它的省力途徑
是犧牲內容。三次逼出的偏差全部是**缺席**，因此跑了數個版本才被發現。

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
