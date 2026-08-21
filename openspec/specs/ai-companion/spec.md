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

### Requirement: 解讀指引與報表定義保持同步

系統 SHALL 確保解讀指引文件所引用的報表，都存在於報表定義中；報表退場、
合併或改名後，指引 SHALL 一併更新。

#### Scenario: report_key 集合對帳

- **WHEN** 執行一致性檢查
- **THEN** 指引文件提到的每個 report_key SHALL 存在於報表定義
- **AND** 指引提到但定義中不存在時，檢查 SHALL 失敗
- **AND** 定義中存在但指引未涵蓋時，SHALL 列出提醒但不使檢查失敗

#### Scenario: 指引不複述報表結構

- **WHEN** 撰寫或修改解讀指引
- **THEN** 指引 SHALL 說明解讀重點與禁止超譯的範圍
- **AND** SHALL NOT 複述欄位清單或呈現形式（那屬報表定義的職責）

### Requirement: 文件權責邊界

系統 SHALL 為描述報表的各份文件定義單一職責，避免同一份知識散落多處：
報表定義負責存在性與欄位、解讀指引負責解讀重點、內容標準負責跨報表寫作規範、
簡報技能文件負責投影片呈現、交付線設計文件負責頁面盤點。

#### Scenario: 新增報表時的文件更新範圍

- **WHEN** 新增或改版一張報表
- **THEN** SHALL 依權責邊界判斷需更新哪幾份文件
- **AND** 一致性檢查 SHALL 能指出未更新的指引

### Requirement: 解讀取證的資料來源

解讀端 SHALL 透過報表列提供的內部識別碼自行取證，資料層 SHALL NOT 預先計算
敘述內容餵給解讀端。

#### Scenario: 設計保護標的由解讀端撰寫

- **WHEN** 解讀端撰寫設計保護策略的標的描述
- **THEN** SHALL 以報表列的設計專利識別碼查詢文獻備註
- **AND** SHALL 讀懂後改寫成可讀敘述，SHALL NOT 照抄整段
- **AND** 資料層 SHALL NOT 在報表層預先抽取或摘要該內容

#### Scenario: 家族判讀由解讀端取證

- **WHEN** 解讀端判斷申請成長屬真實新增或同族延伸
- **THEN** SHALL 依同族識別碼分組取證
- **AND** SHALL NOT 依賴數據表提供家族數欄位
