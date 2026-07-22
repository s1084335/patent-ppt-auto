# WIPS 瀏覽器流程

固定入口：`https://www.wipsglobal.com/servicecn/wap/wapView.wips?trgtFld=devWapField`

1. 以 Playwright MCP 開啟入口並取得 accessibility snapshot。
2. 確認頁面為「标准专利权人代码检索」；登入失效時讓使用者登入或載入既有 storage state。
3. 以標籤／role 尋找「名称/代码检索」輸入框，不使用固定螢幕座標。
4. 填入公司名稱並按「检索」，頁面變動後重新取得 snapshot。
5. 記錄結果列數；多個合理候選時先讓使用者選擇，不擅自合併。
6. 展開目標列，讀取標準申請人代碼、標準公司名稱及全部別稱。
7. 若有分頁、更多或虛擬捲動，走完全部區塊。水平截斷不等於資料截斷，只採 DOM／snapshot 完整文字。
8. 核對 `observed_alias_count` 與 aliases 原始筆數；不一致時設 `complete=false`。

## 登入

- 第一版優先使用 Playwright MCP 專案專屬 persistent profile 或 storage state。
- 未來無人值守登入可由本機配接器在程序內讀 `.env`，建立 storage state 後交給 Playwright MCP。
- Claude、Skill 與 MCP 工具參數不得接收或回傳明文帳密。
- CAPTCHA、雙因素驗證與異常驗證頁一律人工處理。
