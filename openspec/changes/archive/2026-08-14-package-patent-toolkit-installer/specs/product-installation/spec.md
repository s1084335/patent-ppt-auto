## Purpose

讓沒有專案原始碼與開發環境的 Windows 使用者，可用單一安裝包設定 Companion、AI CLI/MCP、產品 skills 與啟動入口。

## ADDED Requirements

### Requirement: INS-001 單一安裝根目錄

Installer SHALL 將產品管理的檔案集中在單一根目錄，並將使用者資料與登入憑證留在各工具的受管位置。

#### Scenario: 完成安裝

- **WHEN** 使用者接受安裝
- **THEN** 所有產品程式、skills、設定模板與 logs SHALL 可由單一根目錄追蹤

### Requirement: INS-002 AI CLI 偵測與選擇

Installer SHALL 偵測支援的 AI CLI；無法唯一決定時要求使用者選擇，不得靜默綁定開發機路徑。

#### Scenario: 同時存在兩個支援 CLI

- **WHEN** 系統偵測到 Claude 與 OpenCode
- **THEN** Installer SHALL 顯示選擇
- **AND** 保存選定 CLI 的可驗證路徑

### Requirement: INS-003 Companion 與 MCP 設定

Installer SHALL 建立 Companion 啟動方式、server/MCP 連線設定與狀態檢查，且不得包入秘密或使用者 token。

#### Scenario: 首次啟動

- **WHEN** 使用者從捷徑開啟產品
- **THEN** Companion SHALL 啟動或確認已在執行
- **AND** 瀏覽器開啟設定的產品網址

### Requirement: INS-004 Skills 由 Runbook 打包

Installer SHALL 只打包產品 skill 的執行 Runbook，不帶入開發路徑、migration 編號或內部驗證指令。

#### Scenario: 檢查安裝後 skill

- **WHEN** 零專案背景的 CLI 讀取 skill
- **THEN** SHALL 能依產品 MCP/API 完成流程
- **AND** 不需要存取開發 repo

### Requirement: INS-005 可更新與解除安裝

Installer SHALL 支援重跑／更新與解除安裝，解除時只移除產品管理檔案與啟動項，不刪使用者產生的專利或報告資料。

#### Scenario: 解除安裝

- **WHEN** 使用者執行 uninstall
- **THEN** Companion 自啟與產品捷徑 SHALL 被移除
- **AND** 使用者資料 SHALL 保留

