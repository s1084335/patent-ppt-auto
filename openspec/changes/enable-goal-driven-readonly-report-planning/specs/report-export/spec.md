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

### Requirement: EXP-018 產後品質驗證決定 PPT 是否可交付

系統 SHALL 在每次 PPT build 後產生 `PptQualityReport`，以 builder manifest、PowerPoint COM 全頁 PNG render 結果、選圖覆蓋、evidence coverage、必要 slot 與版面 warnings 判定 `pass`、`regenerate_partial`、`regenerate_report_version` 或 `blocked_defect`；未通過時不得提供正式交付版本。

#### Scenario: manifest 顯示缺 narrative

- **WHEN** PPT manifest 含 `narrative_missing`
- **THEN** quality report SHALL 標示 fail
- **AND** regeneration plan SHALL 只要求重產對應 `report_key` / variant 的 narrative

#### Scenario: 版面自檢發現重疊

- **WHEN** PPT manifest 含 `text_overlap`、`out_of_bounds` 或 `margin_violation`
- **THEN** quality report SHALL 標示 `blocked_defect`
- **AND** 系統 SHALL 不要求 CLI 自由調整 PowerPoint 幾何

#### Scenario: PowerPoint COM 轉圖失敗

- **WHEN** rendered PNG 頁數與 PPT manifest 頁數不符，或任一頁 render 失敗
- **THEN** quality report SHALL fail
- **AND** 該 PPT 不得進入使用者可接受的正式候選

### Requirement: EXP-019 局部重產必須受 scope lock 約束

系統 SHALL 以 `RegenerationPlan` 明列可重產 targets 與 locked items；CLI 回傳的替換內容 MUST 只涵蓋被標記的 narrative、slide narrative、slot 或 evidence target，不得改動未標記 slide purpose、chart identity、選圖集合或已通過的敘述。

#### Scenario: CLI 擴大修改未標記 slide

- **GIVEN** regeneration plan 只允許重產 `slide-07` 的 narrative
- **WHEN** CLI response 同時改動 `slide-03` 或替換 chart identity
- **THEN** runner SHALL 拒絕該 response
- **AND** 不得保存新的 candidate artifact

#### Scenario: 同一 target 重試仍不合格

- **WHEN** 同一 regeneration target 已完成兩輪局部重產但 quality report 仍 fail
- **THEN** 系統 SHALL 停止自動重產
- **AND** 標示 `blocked_content_defect` 或 `blocked_layout_defect` 供人工或開發處理
