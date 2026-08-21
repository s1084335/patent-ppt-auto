## ADDED Requirements

### Requirement: EXP-017 HTML 報表字級由單一定義處決定

匯出 HTML 報表的字級 SHALL 為：一級標題 28px、二級標題 24px、正文與表格 16px、
圖表內文字 16px。這些值 SHALL 有唯一定義處，各消費端 SHALL 由該處推導，
SHALL NOT 各自重打數值。

#### Scenario: 文字字級符合定義

- **WHEN** 產製 HTML 報表
- **THEN** 一級標題 SHALL 為 28px
- **AND** 二級標題 SHALL 為 24px
- **AND** 正文與表格 SHALL 為 16px

#### Scenario: 圖表內文字與正文同級

- **WHEN** 使用者檢視產出的報表
- **THEN** 圖表內文字的**實際顯示字級** SHALL 為 16px

#### Scenario: 字級只有一個定義處

- **WHEN** 檢視字級的實作
- **THEN** 每個字級 SHALL 只在一處定義
- **AND** 其他使用處 SHALL 由該處推導

### Requirement: EXP-018 圖表內文字級 SHALL 獨立於顯示尺寸

圖表內文字的字級 SHALL 由圖表產生時直接決定，SHALL NOT 由顯示寬度或縮放比反推。
改變圖表的顯示尺寸 SHALL NOT 改變其內部文字的字級。

⚠ 原本「由顯示寬度反推字級」的設計是為了同一份 SVG 同時供簡報使用；該消費端已退場。

#### Scenario: 改變圖寬不改變字級

- **GIVEN** 同一張圖表以兩種不同顯示寬度呈現
- **WHEN** 量測其內部文字字級
- **THEN** 兩者 SHALL 相同

#### Scenario: 字級由產生時決定

- **WHEN** 產生圖表
- **THEN** 內部文字字級 SHALL 直接取自字級定義處
- **AND** SHALL NOT 依賴任何顯示端的寬度設定

### Requirement: EXP-019 圖表尺寸 SHALL 依資料量伸縮

圖表的呈現尺寸 SHALL 依該圖實際承載的資料量決定。資料量大時 SHALL 擴展以維持
可讀性；資料量小時 SHALL 收斂以免佔用過多版面。系統 SHALL 設定尺寸的上界與下界，
使兩端都不失控。

⚠ 本需求 SHALL NOT 以固定高度或單一寬度上限實作——那正是要取代的做法。

#### Scenario: 資料量大時擴展

- **GIVEN** 一張承載大量類別或列的圖表
- **WHEN** 呈現於報表
- **THEN** 尺寸 SHALL 擴展至足以呈現全部標籤
- **AND** 標籤 SHALL NOT 相黏或被截斷

#### Scenario: 資料量小時收斂

- **GIVEN** 一張只有少量類別的圖表
- **WHEN** 呈現於報表
- **THEN** 尺寸 SHALL 收斂，SHALL NOT 佔用與大資料量圖表相同的版面

#### Scenario: 同圖不同資料量得到不同尺寸

- **GIVEN** 同一種圖表分別餵入大量與少量資料
- **WHEN** 兩者皆產出
- **THEN** 兩者的呈現尺寸 SHALL 不同

#### Scenario: 上下界生效

- **WHEN** 資料量超出設定的上界或低於下界
- **THEN** 尺寸 SHALL 停在界限值
- **AND** SHALL NOT 無限擴張或縮至不可讀

### Requirement: EXP-020 數據表 SHALL 預設展開最重要的部分

報表的數據表 SHALL 預設展開該表最具資訊價值的部分，其餘 SHALL 沿用既有的收合方式。
使用者 SHALL 可展開被收合的部分。

#### Scenario: 精華部分預設可見

- **WHEN** 使用者開啟報表
- **THEN** 數據表最具資訊價值的部分 SHALL 已展開
- **AND** SHALL NOT 需要操作才能看見

#### Scenario: 其餘沿用既有收合

- **WHEN** 使用者檢視被收合的部分
- **THEN** 收合與展開的操作方式 SHALL 與既有相同

### Requirement: EXP-021 章節順序 SHALL 依論證品質決定

報表的章節組成與順序 SHALL 依「何種安排能提高判讀品質」決定，SHALL NOT 沿用固定頁序。
未被選用的報表 SHALL NOT 產生空章節。

#### Scenario: 章節依論證安排

- **WHEN** 產製報表
- **THEN** 章節順序 SHALL 反映判讀的論證脈絡

#### Scenario: 沒有資料的章節不出現

- **GIVEN** 某報表未被選用或無資料
- **WHEN** 產製報表
- **THEN** SHALL NOT 產生該章節
- **AND** SHALL NOT 留下標示待補的空白區塊

### Requirement: EXP-026 報表 SHALL 在結論之後、圖表章節之前提供敘述統計

報表 SHALL 在結論之後、各報表章節之前提供一節敘述統計，讓讀者在看圖之前先知道
這份資料的組成。該節 SHALL 包含：

- 各專利類型的件數
- 總件數與家族數
- 法律狀態分布
- 資料的時間範圍
- 受理局數、主題數與分群覆蓋率

敘述統計的數值 SHALL 由確定性計算產生，SHALL NOT 由 AI 產生或估算。既有計算已涵蓋的
量 SHALL 直接消費該計算，SHALL NOT 另行實作第二份。

#### Scenario: 位置在結論與圖表章節之間

- **WHEN** 使用者閱讀報表
- **THEN** 敘述統計 SHALL 出現在結論之後
- **AND** SHALL 出現在第一個圖表章節之前

#### Scenario: 含各專利類型件數

- **WHEN** 呈現敘述統計
- **THEN** SHALL 列出各專利類型的件數

#### Scenario: 含母體規模

- **WHEN** 呈現敘述統計
- **THEN** SHALL 列出總件數與家族數
- **AND** 家族數 SHALL 使用與報表其他處相同的家族口徑

#### Scenario: 含法律狀態分布

- **WHEN** 呈現敘述統計
- **THEN** SHALL 列出各法律狀態桶的件數
- **AND** 狀態桶 SHALL 取自狀態桶的唯一定義處，SHALL NOT 另行列舉狀態字面

#### Scenario: 含時間範圍

- **WHEN** 呈現敘述統計
- **THEN** SHALL 列出資料涵蓋的起訖年

#### Scenario: 含涵蓋範圍指標

- **WHEN** 呈現敘述統計
- **THEN** SHALL 列出受理局數、主題數與分群覆蓋率
- **AND** 分群覆蓋率 SHALL 說明已分群件數與未納入件數

#### Scenario: 數值來自確定性來源

- **WHEN** 產生敘述統計
- **THEN** 每個數值 SHALL 取自既有的確定性計算
- **AND** SHALL NOT 由 AI 產生或估算

#### Scenario: 與各章節數字一致

- **GIVEN** 敘述統計與某圖表章節描述同一個量
- **WHEN** 兩處皆呈現
- **THEN** 數值 SHALL 相同

### Requirement: EXP-022 解讀 SHALL 講得出趨勢與成因

報表解讀 SHALL 對所描述的技術趨勢提出數據依據並說明成因。此要求 SHALL 由既有品質
規則承載，SHALL NOT 新增判準。

#### Scenario: 趨勢判讀有依據且有成因

- **WHEN** 解讀描述一項技術趨勢
- **THEN** SHALL 含支撐該判讀的具體數值
- **AND** SHALL 說明造成該現象的原因

### Requirement: EXP-023 行動建議 SHALL 有可執行內容才寫

解讀中的行動建議 SHALL 只在存在具體可執行內容時出現。系統 SHALL NOT 要求每則解讀
都包含行動建議，SHALL NOT 規定行動建議的句型。

🔴 強制每則都有建議，會逼出對任何資料都成立的萬用句——這是第一世代模板化的直接成因。

#### Scenario: 有可執行內容時寫出具體建議

- **GIVEN** 某項判讀存在具體可執行的後續動作
- **WHEN** 解讀產出
- **THEN** SHALL 寫出該建議
- **AND** 建議 SHALL 具體到可據以行動

#### Scenario: 無可執行內容時不寫

- **GIVEN** 某項判讀沒有具體可執行的後續動作
- **WHEN** 解讀產出
- **THEN** SHALL NOT 為了填滿而產生建議

#### Scenario: 不得出現萬用建議句

- **WHEN** 檢視整份報表的全部解讀
- **THEN** SHALL NOT 出現對任何資料都成立、可互換到其他報表的建議句

#### Scenario: 不規定句型

- **WHEN** 檢視行動建議的相關規則
- **THEN** SHALL NOT 有任何一條規定建議必須使用特定句型或特定起始文字

### Requirement: EXP-024 整體限制說明 SHALL 集中於報表末尾且精簡

描述整份報表資料涵蓋範圍與已知缺漏的整體限制說明 SHALL 集中呈現於報表末尾，
且 SHALL 只保留會影響數字判讀的項目。

⚠ 逐張圖表的口徑註記（該圖的數字如何計算、排除了什麼）SHALL 留在該圖旁邊，
SHALL NOT 併入末尾——註記離開它說明的數字，那個數字就變得無法判讀。

#### Scenario: 整體限制在末尾

- **WHEN** 使用者閱讀報表
- **THEN** 整體限制說明 SHALL 出現在報表末尾
- **AND** SHALL NOT 出現在報表開頭或各章節之間

#### Scenario: 逐圖口徑註記留在原處

- **GIVEN** 某張圖表有影響其數字解讀的口徑註記
- **WHEN** 報表呈現該圖
- **THEN** 該註記 SHALL 與該圖一同呈現
- **AND** SHALL NOT 被移至報表末尾

#### Scenario: 只保留影響判讀的項目

- **WHEN** 產生整體限制說明
- **THEN** SHALL 只包含會影響數字判讀的項目
- **AND** SHALL NOT 逐條羅列不影響判讀的處理細節
