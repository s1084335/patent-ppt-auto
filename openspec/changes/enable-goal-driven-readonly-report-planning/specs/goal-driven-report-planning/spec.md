## Purpose

定義以使用者最大目標與選定圖表為核心的報告規劃能力，使 CLI 能安排論證、唯讀補取敘述證據並產生可驗證的動態 PPT 計畫，同時不取得任何資料寫入權。

## ADDED Requirements

### Requirement: GRP-001 最大目標為報告規劃最高約束

系統 SHALL 以 `ReportBrief` 保存最大目標、受眾、必要章節／方向、頁數限制、workspace/analysis snapshot 與選定圖表；CLI 產生的核心論點、章節與結論 MUST 可回扣最大目標。

#### Scenario: 章節與最大目標無關

- **WHEN** CLI 產生無法說明如何支援最大目標的章節
- **THEN** 規劃驗證 SHALL 失敗並指出不相關章節

### Requirement: GRP-002 使用者選圖是不可遺漏的必要輸入

系統 SHALL 將每張選定圖表的 image artifact、結構化 report data、口徑 metadata、snapshot identity 與 checksum 全部提供給 CLI；每張選圖 MUST 至少出現在一個 slide，未選圖表不得被 CLI 自行加入正式 plan。

#### Scenario: 選定圖表未被使用

- **GIVEN** 使用者選定五張圖表
- **WHEN** CLI plan 只引用其中四張
- **THEN** 系統 SHALL 拒絕該 plan 並列出缺少的 chart identity

#### Scenario: CLI 建議額外圖表

- **WHEN** CLI 判斷目前選圖不足以回答最大目標
- **THEN** CLI MAY 回傳缺圖建議
- **AND** 該圖在使用者重新選取前 MUST NOT 進入正式 PPT plan

### Requirement: GRP-003 CLI 可依目標規劃但不控制幾何

CLI SHALL 可決定論證順序、章節、slide purpose、圖表排序、合頁／拆頁、敘述與建議；實際座標、字級、色彩與元件幾何 MUST 由 deterministic builder 依核准版型解析。

#### Scenario: CLI 輸出任意座標

- **WHEN** CLI response 含任意 x/y/width/height 或未核准版型名稱
- **THEN** 系統 SHALL 拒絕或忽略該幾何指示
- **AND** 不得直接寫入 PPTX

### Requirement: GRP-004 補查資料只作敘述證據

CLI MAY 為撰寫敘述查詢同一 snapshot 內的公司、年度、國家、主題與代表專利證據；補查結果 SHALL 只進 narrative、evidence reference 或資料限制，不得自行轉成未經使用者選取的新圖表。

#### Scenario: 補查結果適合新增圖表

- **WHEN** 唯讀查詢發現值得另外視覺化的趨勢
- **THEN** 輸出 SHALL 將其列為後續選圖建議
- **AND** 本次 PPT 不得自行產生該圖

### Requirement: GRP-005 每個主張可追溯至證據

每個具體數字、具名發現與最終建議 SHALL 引用選定圖表數據或唯讀查詢產生的 evidence reference，並保存 report key、filters、snapshot/version 及必要 row/patent identities。

#### Scenario: 數字沒有來源

- **WHEN** slide narrative 含無法在 evidence manifest 找到的數字或具名對象
- **THEN** 組版前驗證 SHALL 失敗
- **AND** 不得以模型常識補足來源

### Requirement: GRP-006 規劃輸出為不可變候選

CLI SHALL 只回傳結構化 `ReportStrategy`、`SlidePlan`、`EvidenceManifest`、資料限制與缺圖建議；CLI 不得直接更新 DB，平台 runner MAY 在驗證後把候選作為版本化 artifact 保存。

#### Scenario: CLI 嘗試寫入正式資料

- **WHEN** CLI 呼叫寫入、刷新、建 job 或治理工具
- **THEN** 工具層 SHALL 拒絕該呼叫並令工作失敗
- **AND** 正式資料與 latest pointer SHALL 維持不變
