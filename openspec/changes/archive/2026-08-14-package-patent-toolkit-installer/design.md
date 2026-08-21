# Design: 專利工具安裝包

## Context

目前系統可由開發流程啟動，但交付環境仍依賴人工配置 runtime、模型、資料庫與服務。此 change 將既有 backend、worker、frontend 與必要資產封裝成可重複安裝、升級、診斷與移除的產品流程。

## Goals / Non-goals

- 目標：在支援的 Windows 環境完成先決條件檢查、安裝、初始化、啟動與健康檢查。
- 目標：敏感設定不寫入公開 log，失敗時提供可採取行動的診斷與回復方式。
- 非目標：改寫應用架構、打包任意第三方模型授權、無管理權限的所有環境都可安裝。

## Decisions

### 1. 安裝器只編排經驗證的部署單元

應用映像、前端靜態資產、migration 與模型 manifest 必須有版本與 checksum。安裝器不在目標機器臨時抓未鎖版依賴。

### 2. 設定與程式分離

安裝目錄保存版本化程式；資料、artifact、log 與 secrets 放在獨立可保留目錄。升級不得覆蓋使用者資料，移除時預設不刪資料。

### 3. 健康檢查涵蓋完整服務鏈

成功條件不只是 process 存活，必須驗 backend、worker、DB migration head、frontend、artifact write/read 與必要模型可載入。

## Architecture And Packaging Boundaries

- build：產生鎖版 manifest、checksum 與安裝素材。
- installer：preflight、install、configure、migrate、start、health、rollback。
- runtime：沿用既有 backend/worker role 與部署設定，不複製業務邏輯。
- diagnostics：輸出版本、服務狀態與遮罩後設定，不包含 secret value。

## Output Contract

- 安裝成功：版本、安裝位置、服務狀態、health 結果與 UI URL。
- 安裝失敗：失敗階段、可讀原因、log 位置與是否已回復。
- 升級：記錄 from/to version、migration 結果與 rollback 可用性。
- 移除：列出保留的資料與設定位置，不主動清除使用者資料。

## Test Strategy

- 單元：manifest/checksum、設定遮罩、版本比較、命令組裝。
- 封裝：乾淨 Windows VM 的 install/upgrade/uninstall/rollback。
- 整合：匯入小樣本、建立 job、worker 消費、產生並讀回 artifact。
- 驗收：保留 installer log、health report、版本清單與 UI 啟動畫面。

## Risks And Migration

- 風險：模型或映像過大；以明確容量 preflight 與可驗證離線 bundle 處理。
- 風險：migration 後不能直接降版；升級前備份並把不可逆 migration 顯示為阻擋條件。
- 遷移：先建立可重複 build 與 smoke bundle，再加入 UI installer；開發者啟動流程暫時保留。
