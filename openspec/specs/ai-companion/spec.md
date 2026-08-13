# AI Companion Specification

## Purpose

定義需使用使用者本機 AI CLI 的工作類型、領取方式、payload 邊界、回寫與 artifact 持久化。

## Requirements

### Requirement: AIC-001 AI 工作類型單一來源

系統 SHALL 由 `AI_JOB_TYPES` 單一定義需要外部 AI CLI 的工作，Companion runner registry 必須完整覆蓋該集合。最小權限守門 SHALL 從同一集合推導，逐一驗證產品實際執行的 argv 路徑；新增或移除工作類型但未複審權限等級時，測試 MUST 失敗。

#### Scenario: 新增 AI job 未註冊 runner

- **WHEN** `AI_JOB_TYPES` 新增類型但 runner registry 未加入
- **THEN** 守門測試 SHALL 失敗
- **AND** 不得等到正式 job 被領取後才發現

#### Scenario: 新增 AI job 未宣告工具權限

- **WHEN** `AI_JOB_TYPES` 新增類型但最小權限政策尚未宣告該類型
- **THEN** 守門測試 SHALL 失敗
- **AND** 不得以手寫部分 runner 名單略過新類型

### Requirement: AIC-007 CLI 工具權限分級

系統 SHALL 由共用 CLI gateway 提供無工具、只讀檔案、唯讀資料取證與公開網頁查證四種明示權限等級。各 runner MUST 直接依賴 gateway 並選用完成任務所需的最小等級，不得透過另一個 runner 的 re-export 繼承工具權限。

#### Scenario: 資料檔 runner 執行

- **WHEN** runner 透過受控 payload file 提供輸入
- **THEN** 實際 argv SHALL 只允許 `Read`
- **AND** 不得因模組級 legacy wrapper 為空權限而誤判產品路徑

#### Scenario: 集團歸屬公開網頁查證

- **WHEN** 使用者手動啟動公司集團建議
- **THEN** 實際 argv SHALL 只允許 `WebSearch` 與 `WebFetch`
- **AND** 不得取得 shell、檔案、MCP 或資料庫工具

### Requirement: AIC-002 Host-side CLI 執行

系統 SHALL 由 host-side Companion 使用已登入的 Claude 或 OpenCode CLI 執行 AI 工作，一般 backend/worker 容器不得領取這些工作。

#### Scenario: Companion 不在線

- **WHEN** AI 工作已排隊但 Companion 不可用
- **THEN** 工作狀態 SHALL 保持可追蹤
- **AND** readiness／Companion status SHALL 顯示不可用

### Requirement: AIC-003 大 payload 走檔案

系統 SHALL 將可能超過命令列限制的 prompt/payload 寫入受控暫存檔，再把檔案路徑交給 CLI；不得把所有內容硬塞進 command line。

#### Scenario: 大型敘述 payload

- **WHEN** prompt 超過安全命令列長度
- **THEN** CLI SHALL 從 payload file 讀取
- **AND** subprocess 不因 Windows command length 失敗

### Requirement: AIC-004 AI 輸出受契約與人工護欄約束

系統 SHALL 解析結構化 AI 輸出、驗證必要欄位，並依任務語意將草稿、建議或正式結果寫入不同欄位；AI 不得直接改寫需要人工裁決的正式資料。

#### Scenario: 公司中文名建議

- **WHEN** AI 回傳中文名
- **THEN** SHALL 寫入草稿欄
- **AND** 未經人工確認不得進正式顯示

### Requirement: AIC-005 成功必須包含持久化結果

系統 SHALL 只有在必要 DB 寫回或 artifact upload 成功後才將 AI 工作標為 succeeded。

#### Scenario: Narrative 產生但 artifact 上傳為零

- **WHEN** CLI 已產檔但必要 artifact 沒有寫入共享儲存
- **THEN** 工作 SHALL 失敗
- **AND** 不得回報無法由 backend 讀取的假成功

### Requirement: AIC-006 Doctor 與 smoke

系統 SHALL 提供 Companion doctor、heartbeat 與受控 smoke，能驗證 CLI、佇列領取、進度與完成回寫。

#### Scenario: 執行 smoke

- **WHEN** 操作者執行 Companion smoke
- **THEN** smoke SHALL 只領取自己建立的工作
- **AND** 不消耗其他正式 queued AI 工作

