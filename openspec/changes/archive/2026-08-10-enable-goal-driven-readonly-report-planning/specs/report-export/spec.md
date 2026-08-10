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

### Requirement: EXP-021 產後品質驗證決定 PPT 是否可交付

⚠ 2026-08-09 改號（原為 EXP-018）：`separate-web-and-ppt-chart-profiles` 的
「使用者選圖解析為同 Identity 的 PPT Asset」同樣掛 EXP-018——兩者是不同需求，
不得共用編號。兩個規格檔在同一個 commit（3057bbb，08-06）同時引入，無先後可循，
故以**改動成本**決定：本條除自身外沒有任何引用，另一條已被 tasks 與
`tests/test_bundle_profile_lineage.py` 引用。

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

### Requirement: EXP-020 PPT 只有單一入口，最大目標為選填

⚠ 2026-08-09 合併時改號（原為 EXP-018）：同一編號已由 PR #16 的「產後品質驗證」先行合入 master，兩者是不同需求，不得共用編號。

前端 SHALL 只提供**一個**產生 PPT 的入口；最大目標為選填輸入，不得要求使用者在「固定架構」與「依目標規劃」之間選擇路徑。

- 有填最大目標時，系統 SHALL 以該需求為編排重心（論證順序、頁面取捨、敘述皆服務它）。
- 未填最大目標時，系統 SHALL 以**預設策略**規劃，且產出品質 MUST NOT 因此降級——仍須達到參考範例的專業程度（結論先行、Key Player 深入、判讀說明、每頁具名發現與依據）。
- 固定頁序展開 SHALL 僅作為規劃失敗時的保底路徑，MUST NOT 作為「未填目標」的常態路徑。

🔴 2026-08-09 使用者定案：「ppt 入口要統一一個，使用者有需求就以需求為重心，沒需求也要能跑出符合我給你的兩個範例的專業程度」。原 feature flag 式「兩路並存」語意作廢——把系統的不確定推給使用者選路徑，等於要求使用者理解內部實作。

#### Scenario: 未填最大目標

- **WHEN** 使用者未填最大目標即按下產生 PPT
- **THEN** 系統 SHALL 以預設策略產生規劃並組版
- **AND** 產出 SHALL 具備結論先行段落、Key Player 深入與判讀說明
- **AND** SHALL NOT 直接退回固定頁序展開

#### Scenario: 規劃失敗的保底

- **WHEN** 規劃或其驗證失敗
- **THEN** 系統 SHALL 以固定頁序展開產出 PPT 並明確標示「未依規劃編排」
- **AND** 失敗原因 SHALL 出現在工作結果，不得靜默降級
