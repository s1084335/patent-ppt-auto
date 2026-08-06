## Why

匯入、瀏覽專利與分類區已有 API／單元／DB 契約測試，但 `e2e-test-checklist-three-zones.md` 尚無可重複的瀏覽器 E2E，專利 26 欄與真資料顯示也仍待實機驗收。需要把三區完整使用流程變成可重跑、可保存證據的 acceptance suite。

## What Changes

- 建立隔離的 E2E fixture／workspace，從上傳小型 WIPS 檔開始走完整 job 流程。
- 驗證匯入上傳與處理進度、結果統計、workspace 歸屬及錯誤狀態。
- 驗證全庫／workspace 瀏覽、26 欄、原文／正規化值、連結、代表圖 lazy load 與水平捲動。
- 驗證技術／功效分類、主題專利、排除／復原、AI 待複核與 SSE 後資料刷新。
- 保存桌面／行動截圖、network/console、DB 對帳與測試資料清理前清單。

## Capabilities

### New Capabilities

- `system-acceptance`: 定義隔離 fixture、瀏覽器流程、資料對帳、視覺證據與清理授權契約。

### Modified Capabilities

無。

## Scope

涵蓋產品前三區與其直接 job/API；報表與 PPT 有各自 artifact 驗收，不在本 change 重跑全套。

## Non-goals

- 不用 mock browser response 取代真 API/DB 流程。
- 不連正式資料庫或清除使用者資料。
- 不把 E2E 發現的功能 bug 偷夾進同一 change；另開修復 change 並先補 regression test。

## Impact

- 新增 Playwright E2E、隔離資料建立／對帳／清理腳本與 CI/manual profile。
- 需要可啟動的 backend、worker、Companion（AI 案例）與測試 PostgreSQL。

## Activation

預設以測試 DB、測試 storage、固定小樣本執行；AI 可分 deterministic fake 與明確 opt-in 的真 Companion profile。

## Acceptance Gate

桌面與行動 viewport 全流程通過，console 無未解錯誤、API/DB 數量一致、截圖無重疊截字；先展示測試匯入與清理候選，取得使用者同意後才清除資料或 archive。
