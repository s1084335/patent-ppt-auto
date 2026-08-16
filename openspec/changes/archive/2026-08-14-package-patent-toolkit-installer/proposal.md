> ⚠ **2026-08-14 作廢封存（未實作）**：使用者 2026-08-12 定案「使用者只拿 URL、
> 零安裝件」，2026-08-14 確認作廢——中央 Web 平台直接以網址交付，不再打包
> 本機安裝程式。原本要回答的問題現在由誰回答：
> - 「使用者機器怎麼裝起來」→ 不裝。瀏覽器開中央平台 URL 即用。
> - 「Companion 怎麼佈署」→ `scripts/companion_startup_install.ps1`
>   （啟動資料夾捷徑，純使用者層級；deck design 4-0b 記載其為 CLI token
>   可用性的前提）。
> - 「deck skill 怎麼到使用者機器」→ 不需要：deck 產線在伺服器側
>   （`skills/html-report-to-deck/` 隨 repo 部署），使用者只下載 pptx。
> - 「字型／Chromium 等機器依賴」→ 屬部署前置（deck design 4-0b 環境清單），
>   由架站方在伺服器側處理，非使用者端安裝件。
> 留痕：`.agents/context/decisions.md` 2026-08-12「產物存放整合規劃」；
> installer-spec.md 背景 context 保留於 `.agents/context/`（歷史參考）。

## Why

目前已有 Companion 與 launcher 腳本，但尚未形成可在全新 Windows 機器交付的安裝包；MCP、CLI 偵測、啟動與移除流程仍依賴開發機背景。

## What Changes

- 建立可重複產製的 Windows installer/package。
- 安裝使用者端 Companion、AI CLI/MCP 設定與產品 skills 到單一根目錄。
- 偵測可用 AI CLI，無法唯一判定時讓使用者選擇。
- 建立啟動、健康檢查、開頁面、更新與解除安裝流程。
- 打包時機械抽取產品 skills 的 Runbook，不攜帶開發機路徑。

## Capabilities

### New Capabilities

- `product-installation`：定義 Windows 使用者端安裝、設定、啟動、更新與移除契約。

### Modified Capabilities

- `ai-companion`：補足安裝後的 Companion 發現、啟動與狀態契約。

## Scope

Installer build、Companion/launcher scripts、MCP config、skill packaging、捷徑與全新機器驗收。

## Non-goals

- 不把 Companion 移入 server container。
- 不在安裝包內保存使用者 token 或 CLI 登入憑證。
- 不新增第二套產品執行架構。

## Impact

影響 Windows 檔案系統、啟動項、MCP/AI CLI 設定與產品發布；需可完整解除安裝且不刪使用者資料。

## Activation

需要建立簽出／版本化產物並在乾淨 Windows 使用者環境實測；server URL 與必要 public config 由安裝時輸入或部署設定提供。

## Acceptance Gate

以全新機器完成安裝、CLI 選擇、Companion 啟動、MCP 連線與一次完整產品流程，再驗解除安裝與重裝。

