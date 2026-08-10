## ADDED Requirements

### Requirement: AIC-009 報告規劃工作使用隔離工具白名單

AI Companion SHALL 為 goal-driven report planning 建立獨立工作類型，只提供 payload file 讀取與 report-research 唯讀工具；shell、一般 filesystem 寫入、混合讀寫 MCP 及其他工具 MUST 不可用。

#### Scenario: CLI 要求未授權工具

- **WHEN** CLI 嘗試呼叫寫入型 MCP、shell 或任意 filesystem write
- **THEN** Companion SHALL 拒絕工具呼叫並記錄遮罩後 audit event

### Requirement: AIC-010 CLI 結構化規劃必須先驗證再保存

Companion SHALL 驗證 CLI response 的 strategy、slides、chart coverage、evidence references 與資料限制；只有通過後 runner 才可將結果保存為候選 artifact，CLI 本身不得直接持久化。

#### Scenario: CLI 回傳無效 chart identity

- **WHEN** SlidePlan 引用不在使用者選圖資料包的 chart identity
- **THEN** 工作 SHALL 失敗
- **AND** 不建立正式或候選 PPT artifact

### Requirement: AIC-011 不支援受控工具的 CLI 必須明確失敗

若所選 CLI provider 無法保證 MCP/tool allowlist、圖片讀取或結構化輸出契約，系統 SHALL 在派工前標示不支援，不得退回擁有廣泛工具權限的執行方式。

#### Scenario: Provider 無法限制工具

- **WHEN** CLI capability check 顯示無法套用唯讀工具白名單
- **THEN** 系統 SHALL 不啟動報告規劃工作
- **AND** 回報可選的相容 provider 或能力缺口

### Requirement: AIC-012 Companion 保存產後驗證與重產決策 audit

Companion runner SHALL 保存每次 build 的 `PptQualityReport`、rendered PNG manifest、regeneration decision、scope lock、CLI replacement response 與接受／拒絕原因；CLI 不得自行宣告 quality gate 通過，也不得直接保存局部重產結果。

#### Scenario: CLI 回傳超出 regeneration scope

- **WHEN** CLI replacement response 修改未列入 `RegenerationPlan.targets` 的內容
- **THEN** Companion SHALL 拒絕該 response
- **AND** audit SHALL 記錄被拒絕的 target、locked item 與遮罩後原因

#### Scenario: Quality gate 通過

- **WHEN** `PptQualityReport.decision` 為 `pass`
- **THEN** Companion MAY 保存候選 PPT artifact
- **AND** audit SHALL 連結 PPTX manifest、PNG manifest、evidence manifest 與 quality report
