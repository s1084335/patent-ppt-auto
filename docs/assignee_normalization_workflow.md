# 專利權人代碼正規化補全流程

## 目標

當專利資料匯入系統後，系統需自動掃描現有公司名稱與各種專利權人欄位，先用既有專利權人代碼對照表嘗試正規化；若找不到可用對照，才觸發 Claude Code CLI + Skills + Playwright MCP 的補全流程，到 WIPS 標準專利權人代碼檢索頁查詢並整理結果，最後寫回資料庫作為後續報表與前端顯示使用。

此流程的目標是讓公司/專利權人名稱正規化變成公司內部可重複、可追溯、可擴充的正式解法，而不是每次人工臨時查表。

最終口徑：

```text
原有專利資料值不動。
另有一張專利權人/公司對照表保存 WIPS 代碼、標準公司名稱與別稱。
之後報表統計、公司排名、公司×國家矩陣、研發能量等，都以正規化後公司名稱計算。
```

## 參考來源

- WIPS 標準專利權人代碼檢索頁：
  `https://www.wipsglobal.com/servicecn/wap/wapView.wips?trgtFld=devWapField`
- 現有格式範例：
  `docs/reference/專利權人代碼對照表_合併.xlsx`
- 範例 Excel 欄位：

```text
申請人代碼
公司名稱
別稱
```

## 角色分工

| 組件 | 責任 | 不負責 |
|---|---|---|
| 匯入流程 | 掃描本次匯入資料中的申請人、專利權人、受讓人等公司欄位 | 不直接改寫 raw/core 原始名稱 |
| 正規化引擎 | 對照既有 `company_aliases` / 專利權人代碼表，判斷是否已有可用正規化 | 不猜測高風險公司合併 |
| Claude Code CLI | 依 Skill 執行 WIPS 查詢、整理候選結果、輸出結構化資料 | 不繞過審核直接覆蓋原始資料 |
| Skills | 固定查詢步驟、欄位格式、判斷規則與輸出 JSON schema | 不保存密碼或機密 token |
| Playwright MCP | 控制瀏覽器進入 WIPS 頁面、輸入公司名、展開結果、讀取頁面資料 | 不作為資料庫寫入層 |
| 後端 / DB | 接收結構化結果，寫入公司代碼對照資料表與任務紀錄 | 不把補全結果寫回 raw_records / patent_people 原始欄位 |

## 觸發條件

資料匯入後，系統對下列欄位做名稱掃描：

```text
申請人
標準化申請人
最近專利權人
標準當前專利權人
受讓人
最近受讓人
其他 WIPS 人名/公司欄位
```

流程：

1. 抽出公司名稱候選，保留來源欄位與 patent_id / raw_record_id。
2. 以既有對照表查詢是否已有 match。
3. 若 match 成功，套用既有 `申請人代碼 / 公司名稱 / 別稱` 對照。
4. 若 match 失敗或信心不足，建立「專利權人代碼補全任務」。
5. 補全任務交由 Claude Code CLI + Skill + Playwright MCP 查 WIPS。
6. 查詢結果以固定格式回傳後，後端寫入資料庫。

## WIPS 查詢流程

Claude Code CLI 依 Skill 執行：

1. 開啟 WIPS 標準專利權人代碼檢索頁。
2. 在「名稱/代碼檢索」輸入公司名稱，例如 `rexon`。
3. 執行搜尋。
4. 展開搜尋結果。
5. 讀取標準申請人代碼、標準公司名稱、括號內代碼或補充資訊、底下列出的別稱。
6. 整理成固定格式。

從畫面範例可讀出類似資料：

```text
申請人代碼: UN116754
公司名稱: REXON INDUSTRIAL CORP., LTD.
別稱:
- Lishan Industry Co Ltd
- REXON IND CO LTD
- REXON IND CORP LTD
- REXON INDUSTRIAL CORP LTD
- Rexon Industrial Corporation Ltd
```

## 寫回格式

第一版與既有 Excel 對照表保持一致：

| 欄位 | 說明 |
|---|---|
| 申請人代碼 | WIPS 標準申請人 / 專利權人代碼，例如 `UN116754` |
| 公司名稱 | WIPS 標準公司名稱，例如 `REXON INDUSTRIAL CORP., LTD.` |
| 別稱 | 可對應到同一代碼的公司名稱變體 |

資料庫第一版可沿用 `derived_layer.company_aliases` 的概念；若後續需要完整稽核，再加任務表記錄 WIPS 查詢來源、查詢時間、查詢字串、操作者、confidence、review_status。

## 寫入原則

- 正規化結果只用於報表統計、前端顯示與公司聚合。
- 不覆蓋 `raw_records.raw_data`。
- 不覆蓋 `core_layer.patent_people` 中來自 WIPS 的原始公司/人名欄位。
- 不改寫既有專利的申請人、專利權人、受讓人等來源欄位值。
- 專利權人對照表是獨立 mapping layer；報表查詢時透過 mapping 取得正規化公司名稱。
- 同一 `申請人代碼 + 公司名稱 + 別稱` 不重複寫入。
- 若 WIPS 回傳多個可能公司，標記為 `review_required`，由使用者確認。
- 若 WIPS 無結果，保留補全任務狀態與查詢字串，不強行建立別稱。

## 報表統計口徑

報表不直接用原始專利權人字串做聚合，而是依序決定顯示與統計用公司名稱：

```text
原始專利權人 / 申請人 / 受讓人
→ 專利權人對照表匹配別稱
→ WIPS 代碼對應標準公司名稱
→ normalized_company_name
→ 報表統計 group by normalized_company_name
```

此口徑適用：

- 申請人排名
- 現專利權人排名
- 公司×國家矩陣
- 企業研發能量
- 生命週期 / 公司年度活躍度
- 後續所有公司維度報表

若找不到對照，第一版可 fallback 使用原始公司名稱，但需標記為 `unmapped`，讓後續補全流程處理。

## 前端顯示

前端可在匯入結果或資料品質頁顯示：

| 狀態 | 顯示 |
|---|---|
| 已有對照 | 顯示正規化後公司名稱與代碼 |
| 待 WIPS 補全 | 顯示待查公司數 |
| 查詢成功待確認 | 顯示候選代碼、標準名稱、別稱數 |
| 無結果 | 顯示查詢字串與人工處理入口 |
| 已寫入 | 顯示寫入筆數與對照來源 |

## 驗收標準

1. 匯入資料後能列出未被既有對照表覆蓋的公司/專利權人名稱。
2. Claude Code CLI 可依 Skill 透過 Playwright MCP 查詢 WIPS。
3. 查詢結果能輸出為固定 JSON 或表格格式。
4. 寫入資料庫後，`company_aliases` 類對照資料可新增 `申請人代碼 / 公司名稱 / 別稱`。
5. 重新整理報表基礎表後，申請人/專利權人顯示名稱可用新增對照正規化。
6. 原始 WIPS 欄位值不被覆蓋。

## 暫不實作項目

- 不在本文件實作 Playwright MCP 腳本。
- 不在本文件新增 migration。
- 不處理 WIPS 登入、授權與帳號管理。
- 不自動合併多個疑似相同但 WIPS 代碼不同的公司。

## 2026-07-17 最終定案：專利權人正規化使用兩個 MCP

最終架構採兩個 MCP 並行，不把 Playwright 瀏覽器操作硬塞進 Central Patent MCP Server。

```text
Claude Code
├─ Central Patent MCP Server
│  ├─ clustering tools
│  ├─ reporting tools
│  └─ assignee normalization DB tools
│
└─ Playwright MCP
   └─ WIPS browser automation
```

分工固定如下：

- `Central Patent MCP Server`：負責資料庫與業務工具，包括掃描未正規化公司、讀取唯一一張專利權人/公司對照表、建立待補全任務、寫入對照表、提供報表使用的正規化公司名稱。
- `Playwright MCP`：只負責瀏覽器自動化，包括開啟 WIPS、搜尋公司名稱、展開標準申請人結果、讀取 WIPS 畫面資料。
- `Claude Code`：負責協調兩個 MCP，將 Playwright MCP 讀到的 WIPS 結果整理成固定 schema，再交給 Central Patent MCP Server 寫入資料庫。

資料庫對照表原則：DB 內只能有一張專利權人/公司對照表。原有專利的公司、申請人、專利權人、受讓人等來源欄位值不動；報表統計透過這張對照表取得 `normalized_company_name` 後再 group by。
