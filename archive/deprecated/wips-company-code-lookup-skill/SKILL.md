---
name: wips-company-code-lookup
description: Coordinate Claude Code, Playwright MCP, and Central Patent MCP to search WIPS standardized applicant codes, capture all company aliases, preview duplicate classifications, and write only approved records into the patent project's single company alias mapping table. Use for WIPS company-code lookup or unresolved assignee normalization.
---

# WIPS 公司代碼檢索

以 Claude Code 協調兩個 MCP：Playwright MCP 只讀取 WIPS 畫面，Central Patent MCP 只處理業務規則與資料庫。預設只產生 preview，使用者明確確認後才能寫入。

## 執行前讀取

- 每次先讀 `references/responsibilities.md`，遵守兩個 MCP 的固定邊界。
- 擷取 WIPS 時讀 `references/browser-workflow.md`。
- 建立結果與判斷重複時讀 `references/result-contract.md`。

## 核心流程

1. 從使用者輸入取得公司名稱；若未提供，透過 Central Patent MCP 取得待正規化公司清單。
2. 確認兩個 MCP 都可用。若 Central Patent MCP 尚未提供公司正規化工具，停止並回報缺口，不得改用直接 SQL 繞過。
3. 先由 Central Patent MCP 讀取既有公司對照資料，建立本次查重基準。
4. 由 Playwright MCP 開啟 WIPS、處理登入狀態、搜尋公司名稱並展開結果。
5. 讀取標準申請人代碼、標準公司名稱及全部別稱，核對畫面觀察筆數與實際擷取筆數。
6. 依結果契約整理資料。名稱旁的 `EN...` 等來源結果代碼只留在 evidence，不得取代 `UN...` 標準申請人代碼。
7. 交給 Central Patent MCP 執行 preview，分類為 `exact_duplicate`、`normalized_collision`、`code_conflict`、`to_insert` 或 `rejected`。
8. 向使用者顯示 preview。未收到明確寫入指示時停止於 dry run。
9. 獲准後只讓 Central Patent MCP 寫入 `to_insert`，再按標準申請人代碼回讀驗證。

## 不可違反事項

- Playwright MCP 不得查詢或寫入資料庫。
- Central Patent MCP 不得控制瀏覽器或解析 WIPS 頁面。
- Claude Code 不得直接執行 SQL 取代 Central Patent MCP 的業務工具。
- 不得改寫原始專利的申請人、專利權人、受讓人等來源欄位。
- 不得建立第二張公司／專利權人對照表。
- `normalized_collision` 與 `code_conflict` 只能進人工審查，不得自動刪除、覆寫或合併。
- CAPTCHA、雙因素驗證或異常驗證頁一律暫停，交由使用者處理。
- 不得讀取、顯示或傳遞實際 `.env` 帳密；登入憑證只能由本機登入配接器在程序內使用。

## 完成回報

回報查詢字、標準申請人代碼、標準公司名稱、觀察別稱數、唯一別稱數、各重複分類筆數、待新增數、實際新增數、登入模式及寫入後回讀結果。未寫入時明確標示 `dry_run`。
