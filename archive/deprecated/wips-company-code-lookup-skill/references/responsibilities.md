# 兩個 MCP 的責任邊界

```text
Claude Code
├─ Central Patent MCP Server
│  └─ 公司正規化業務與資料庫工具
└─ Playwright MCP
   └─ WIPS 瀏覽器自動化
```

| 元件 | 負責 | 禁止 |
|---|---|---|
| Claude Code / Skill | 協調兩個 MCP、整理固定 schema、展示 preview、取得寫入確認、彙整結果 | 直接 SQL、保存帳密、自行發明第三套去重規則 |
| Playwright MCP | 開啟 WIPS、登入狀態、搜尋、展開結果、讀取完整畫面資料 | 查 DB、判定正式重複、寫 Excel 或 DB |
| Central Patent MCP | 掃描未正規化公司、讀取唯一對照表、查重、preview、核准後寫入、回讀驗證、提供報表 mapping | 操作瀏覽器、抓取 WIPS DOM、接收明文帳密 |
| Backend application service | 作為去重與持久化的唯一事實來源，供 Central Patent MCP 與檔案匯入器共用 | 依不同入口維護不同去重語意 |

## Central Patent MCP 能力契約

後端至少需要下列獨立能力：讀取待處理公司、讀取現有對照、執行 preview、寫入已確認的 preview、按標準申請人代碼回讀驗證。實際 tool 名稱可依專案命名調整，但語意不得混合。

若寫入工具尚未存在，Skill 只能完成瀏覽器擷取與 JSON preview，不得用 shell、Python 臨時腳本或直接 SQL 繞過。

## 資料所有權

- DB 內只使用 `derived_layer.company_aliases` 作為公司／專利權人對照表。
- 原始專利欄位維持原值；報表透過 mapping 取得 `normalized_company_name`。
- WIPS 擷取證據、來源網址與執行紀錄屬 audit／task artifact，不另建第二張別稱對照表。
