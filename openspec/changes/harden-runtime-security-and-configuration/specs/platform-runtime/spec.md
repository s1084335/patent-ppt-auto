## ADDED Requirements

### Requirement: PRT-009 部署角色設定 fail-fast

系統 SHALL 區分明確 local-development 與 deployment role；deployment role 缺 `DATABASE_URL`、必要 secret 或角色設定時 MUST 在啟動階段失敗，不得靜默 fallback 到 localhost。

#### Scenario: Worker 容器漏 DATABASE_URL
- **WHEN** `APP_ROLE=worker` 且未提供部署資料庫設定
- **THEN** 程序 SHALL 以非零狀態退出並指出缺少的設定名稱，不得開始 claim loop

#### Scenario: Report-research MCP 漏 reader credential
- **WHEN** deployment 啟用 report-research MCP 但未提供獨立 reader identity，或只提供一般應用 DB credential
- **THEN** 程序 SHALL 以非零狀態退出
- **AND** 不得 fallback 到一般 `DATABASE_URL` 或啟動混合讀寫 MCP profile

### Requirement: PRT-010 AI 寫入端點預設受保護

系統 SHALL 在 deployment role 驗證所有可建立 AI 工作或寫回結果的端點；未授權請求 MUST 被拒絕，credential 不得出現在 URL、前端持久儲存或一般 log。

#### Scenario: 未帶 credential 建立 AI job
- **WHEN** 未授權 client 呼叫受保護 AI 寫入端點
- **THEN** 系統 SHALL 回 401 或 403，且不得建立 job

### Requirement: PRT-011 Readiness 顯示必要依賴

系統 SHALL 以遮罩後資訊分別回報 DB、queue、artifact 與角色必要依賴的 ready/degraded/not-ready 狀態；process alive 不得等同 ready。

#### Scenario: DB DNS 解析失敗
- **WHEN** 程序仍存活但無法解析或連接 DB
- **THEN** readiness SHALL 為 not-ready/degraded 並提供不含 credential 的原因分類
