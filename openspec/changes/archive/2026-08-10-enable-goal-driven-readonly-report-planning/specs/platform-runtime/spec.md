## ADDED Requirements

### Requirement: PRT-012 報告研究工具唯讀邊界由工具層與憑證隔離保證

系統 SHALL 以獨立 report-research MCP profile 暴露唯讀工具；該 profile MUST NOT 暴露任何寫入、刷新、產製或 shell/filesystem-write 工具。報告規劃 CLI MUST NOT 持有資料庫連線憑證——所有 DB 存取一律經伺服器端 MCP 工具，credential 只存在於伺服器端環境變數，不得進入 payload、prompt 或 CLI 工作目錄。

🔴 **2026-08-09 使用者定案（規格回寫）**：原條文要求「工具 MUST NOT 接受 SQL 字串」，被實測推翻——照該條實作出來的七支工具讀的全是 `report_data.json`（引擎彙總好的報表快照），**沒有一支查資料庫**，於是「讓 CLI 去資料庫找證據來寫簡報」這個最大目標並不成立。使用者定案「整合到 MCP 去，包括敘述線也是」後，新增 `query_database` 一支收 SQL 的唯讀工具（原敘述線靠 `Bash(uv run:*)`＋`query_patents.py` 取得的能力搬進 MCP，不因換通道而縮權）。

⇒ 修正後的邊界：**快照型工具** MUST 收 typed 參數且 MUST NOT 接受 SQL；**`query_database`** MAY 接受 SQL，但 MUST 限單句 `SELECT`／`WITH`，MUST 在**該筆交易內**以 `SET TRANSACTION READ ONLY` 與 `SET LOCAL statement_timeout` 強制唯讀與逾時，且 MUST 明示截斷（`truncated`）。唯讀性的保證從「不收 SQL」改為「**交易層強制唯讀＋語法前置檢查**」——前者只是讓工具不好被誤用，後者才是真正擋得住寫入的那一層。

🔴 **2026-08-09（A6 實測）二次修正**：原文寫「由**連線層**強制 `default_transaction_read_only`」，實測**不成立**。本專案 DSN 走 Supabase transaction pooler（6543），pooler **忽略連線字串的 startup options**（`-c`）——繞過語法檢查後 UPDATE／INSERT／CREATE／DELETE **全部執行成功**，`statement_timeout` 也沒有作用。也就是說在那之前，「真護欄」只存在於註解裡，實際只有可繞過的語法檢查一道。

⚠ 這是「規格與現實不符」最危險的一種：文件宣稱有兩層防護，維護者據此放心開放自由 SQL，而第二層根本不存在——**且不會有任何錯誤訊息**（連線成功、查詢正常，只有真的去寫才會發現）。改綁交易後實測四種寫入全被 DB 拒絕、300ms 逾時亦生效。

🔴 **2026-08-07 使用者裁決（規格回寫）**：原條文要求另建 DB reader role 作第二層邊界。正式部署為**公司內網自管伺服器、單一組織使用**，且 CLI 依架構本就拿不到 credential，額外 role 的維運成本（多一組密碼輪替、grants migration、漂移守門）大於邊際安全效益 ⇒ **不採 DB reader role**。若日後開放外部存取、多租戶或把 MCP server 移到使用者端執行，本條須重新評估並回復雙層要求。

#### Scenario: 工具清單不得含寫入能力

- **WHEN** report-research MCP profile 列出可用工具
- **THEN** 清單 SHALL 只含 catalog／preview／query／evidence 類唯讀工具
- **AND** allowlist contract test SHALL 在出現任何 save／refresh／generate／apply／shell／write 工具時失敗

#### Scenario: 唯讀強制必須在交易層（2026-08-09 新增）

⚠ 動因：連線字串的 startup options 在 pooler 後面會被丟棄，而這**不會有任何
錯誤訊息**——連線成功、查詢正常，只有真的去寫才會發現護欄不存在。

- **WHEN** `query_database` 或查詢閘道對資料庫執行查詢
- **THEN** 該筆交易 SHALL 先執行 `SET TRANSACTION READ ONLY` 與 `SET LOCAL statement_timeout`
- **AND** SHALL NOT 依賴連線字串的 `-c default_transaction_read_only`（pooler 會忽略）
- **AND** 契約測試 SHALL 檢查實際的連線呼叫未使用該 startup option

#### Scenario: CLI 不持有資料庫憑證

- **WHEN** 規劃 CLI 啟動並取得其 MCP config 與工作目錄
- **THEN** 其可見設定與檔案 SHALL NOT 含 DB 連線字串、密碼或 service key
- **AND** 契約測試 SHALL 驗證 payload 與 MCP config 不含 credential 欄位

#### Scenario: 取證通道必須真的通得到（2026-08-09 新增）

⚠ 本 scenario 的動因：research profile 曾經「建了 server 實例卻沒有啟動路徑」，
且 headless 白名單沒放行 `mcp__*` 工具——規格與 prompt 都寫得像能查，實際上
一支都呼叫不到，而且**不會報錯**，CLI 照樣產出看似合理的內容。

- **WHEN** 規劃或敘述 CLI 以 headless 模式啟動
- **THEN** 其 argv SHALL 同時包含 `--mcp-config` 與含 `mcp__*` 的 `--allowedTools`
  ——只放行工具卻不掛 config 是靜默失效
- **AND** research profile SHALL 具備可執行的啟動路徑，且該 profile 啟動後
  SHALL 註冊 allowlist 上的全部工具
- **AND** 契約測試 SHALL 驗證取證等級的白名單不含 `Bash`（取證改走 MCP 後，
  以 shell 前綴放行查詢閘道的舊通道不得復活）

#### Scenario: 工具擴權漂移

- **WHEN** 日後有人在 report-research profile 註冊新工具
- **THEN** allowlist contract test SHALL 因清單變動而失敗，強制人工複審後才可放行
