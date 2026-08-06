## ADDED Requirements

### Requirement: AIC-007 安裝後可自我診斷

系統 SHALL 讓已安裝 Companion 能以 doctor 驗證 CLI 路徑、登入可用性、server 連線、heartbeat 與 job type 支援。

#### Scenario: CLI 路徑失效

- **WHEN** 使用者移動或移除原選定 CLI
- **THEN** doctor SHALL 回報具體失敗
- **AND** 不把 Companion 標示為 healthy

