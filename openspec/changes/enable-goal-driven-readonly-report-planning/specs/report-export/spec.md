## ADDED Requirements

### Requirement: EXP-015 PPT 消費經驗證的動態 SlidePlan

系統 SHALL 以通過 schema、最大目標、選圖完整性、證據與容量驗證的 `SlidePlan` 決定頁序；builder 只從核准版型集合解析幾何，且相同輸入與 theme version MUST 產生可重現結構。

#### Scenario: 部分圖表適合合頁

- **WHEN** CLI 將兩張選圖安排在同一 slide 且核准版型容量允許
- **THEN** builder SHALL 使用對應多圖版型
- **AND** manifest SHALL 記錄兩個 chart identities 與該頁目的

### Requirement: EXP-016 選圖完整性與 evidence manifest 為組版閘門

正式 PPTX SHALL 在全部選圖已配置、無未選圖、每個數字可追溯且 snapshot 一致時才可產生；輸出 manifest SHALL 保存最大目標、plan version、slide identity、chart identities 與 evidence references。

#### Scenario: 規劃引用過期證據

- **WHEN** evidence reference 的 snapshot/version 與選圖資料包不同
- **THEN** 系統 SHALL 阻止正式組版並標示 stale evidence

### Requirement: EXP-017 範例只作品質參考

系統 MAY 將既有兩份範例的風格、資訊密度與論證品質作為參考，但 SHALL 不要求固定複製其章節、頁數、圖表或逐頁位置；使用者最大目標與本次選圖優先。

#### Scenario: 範例頁序不符合本次目標

- **WHEN** 範例順序與本次最大目標或選圖證據鏈衝突
- **THEN** CLI SHALL 採用可說明的本次 plan
- **AND** 不得為仿製範例加入無證據頁面
