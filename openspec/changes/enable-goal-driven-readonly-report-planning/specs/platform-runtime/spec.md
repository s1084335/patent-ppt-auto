## ADDED Requirements

### Requirement: PRT-012 報告研究工具唯讀邊界由工具層與憑證隔離保證

系統 SHALL 以獨立 report-research MCP profile 暴露唯讀工具；該 profile MUST NOT 暴露任何寫入、刷新、產製或 shell/filesystem-write 工具，且工具 MUST NOT 接受 SQL 字串。報告規劃 CLI MUST NOT 持有資料庫連線憑證——所有 DB 存取一律經伺服器端 MCP 工具，credential 只存在於伺服器端環境變數，不得進入 payload、prompt 或 CLI 工作目錄。

🔴 **2026-08-07 使用者裁決（規格回寫）**：原條文要求另建 DB reader role 作第二層邊界。正式部署為**公司內網自管伺服器、單一組織使用**，且 CLI 依架構本就拿不到 credential，額外 role 的維運成本（多一組密碼輪替、grants migration、漂移守門）大於邊際安全效益 ⇒ **不採 DB reader role**。若日後開放外部存取、多租戶或把 MCP server 移到使用者端執行，本條須重新評估並回復雙層要求。

#### Scenario: 工具清單不得含寫入能力

- **WHEN** report-research MCP profile 列出可用工具
- **THEN** 清單 SHALL 只含 catalog／preview／query／evidence 類唯讀工具
- **AND** allowlist contract test SHALL 在出現任何 save／refresh／generate／apply／shell／write 工具時失敗

#### Scenario: CLI 不持有資料庫憑證

- **WHEN** 規劃 CLI 啟動並取得其 MCP config 與工作目錄
- **THEN** 其可見設定與檔案 SHALL NOT 含 DB 連線字串、密碼或 service key
- **AND** 契約測試 SHALL 驗證 payload 與 MCP config 不含 credential 欄位

#### Scenario: 工具擴權漂移

- **WHEN** 日後有人在 report-research profile 註冊新工具
- **THEN** allowlist contract test SHALL 因清單變動而失敗，強制人工複審後才可放行
