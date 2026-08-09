## Why

現行 `ai:report_ppt` 只能消費 runner 預先整理的固定 `report_data.json`、`narratives.json` 與 page slots，無法依使用者的最大目標自行安排論證，也不能在撰寫敘述時補取必要證據。需要把 CLI 升級為受控的報告規劃代理，同時以工具面與資料庫權限強制維持唯讀。

## What Changes

- 新增 `ReportBrief`，以最大目標、受眾、章節／方向、頁數限制、workspace/analysis snapshot 與使用者選定圖表作為單一任務輸入。
- 所有使用者選定圖表的圖片 artifact 與結構化數據都交給 CLI；CLI 可以排序、合頁或拆頁，但不得遺漏或自行加入未選圖表。
- CLI 依最大目標產生 `ReportStrategy`、章節論證、動態 slide plan、具名敘述與建議，不再被兩份範例或固定 `PAGE_LAYOUT` 當成內容模板綁死。
- CLI 可透過獨立唯讀 report-research MCP 工具補查敘述證據；不得取得 DB credential、執行任意 SQL、刷新 derived data、建立工作或寫入任何資料。
- 每個敘述與建議必須引用選定圖表數據或唯讀查詢的 evidence reference；平台在組版前驗證證據、snapshot、選圖完整性與容量。
- 保留 deterministic builder：CLI 規劃內容與版型意圖，程式從核准版型中解析實際幾何並組成 PPTX。
- 新增產後品質驗證：builder manifest、PowerPoint COM 全頁 PNG 與 evidence/coverage 檢查必須彙整為 `PptQualityReport`；未通過時 runner 產生結構化 `RegenerationPlan`，只允許 CLI 重產被標記的 narrative、slide narrative 或 evidence，不得擴大修改已鎖定內容。

## Capabilities

### New Capabilities

- `goal-driven-report-planning`: 定義最大目標驅動的研究計畫、選圖完整性、唯讀補證據、slide plan 與證據追溯契約。

### Modified Capabilities

- `patent-reporting`: 增加供 CLI 探索的語意報表目錄、snapshot-bound 唯讀證據查詢與選圖資料包契約。
- `report-export`: 從固定全域頁序／slots 改為經驗證的動態 slide plan，仍由 deterministic builder 組版。
- `ai-companion`: 增加報告規劃工作、最小工具白名單、禁止 CLI 寫 DB 與結構化輸出驗證。
- `platform-runtime`: 增加獨立唯讀 MCP profile、資料庫 reader role 與權限漂移守門。

## Scope

涵蓋 ReportBrief、選定圖表／數據封裝、報告規劃 runner、唯讀 MCP 工具、reader DB role、evidence manifest、動態 slide schema、builder 接線與前端任務／預覽契約。

## Non-goals

- 不讓 CLI 修改專利、公司、主題、報表、workflow、artifact 或其他資料庫資料。
- 不允許 CLI 自行選擇或產生使用者未選的圖表。
- 不讓 CLI 輸出任意 PowerPoint 座標、字級、色彩或繞過 deterministic builder。
- 不恢復市場資料線、痛點板或已否決的技術×功效矩陣。
- 不把兩份既有範例逐頁複製為固定產品模板；它們只保留為品質與風格參考。
- 不讓 CLI 直接判定 PPT 可交付；交付與是否需要局部重產由 runner 的 schema、scope、evidence、chart coverage 與產後 quality gate 決定。

## Impact

- `backend/app/worker/ai_report_ppt_runner.py` 與 Companion job registry/payload。
- `backend/app/mcp_server/` 新增獨立 read-only server/profile 與 report evidence tools；現有混合讀寫 MCP 不直接暴露給本工作。
- `backend/app/reports/` 的 report catalog、snapshot/query broker 與 evidence serialization。
- Alembic/部署設定新增最小權限 DB role/grants；不搬資料、不重匯專利，rollback 撤銷新 grants/profile 即可。
- `skills/patent-report-ppt/` 改為消費經驗證 slide plan 與核准版型，不由 CLI 控制幾何。
- 前端新增最大目標、章節方向與選圖資料包提交／規劃結果檢視。

## Activation

先以既有 runner 作 fallback，新增唯讀工具與 planning output 後以 feature flag 啟用。部署前建立 reader role、套用 grants、驗證無寫入權，再重產一份代表性報告；不需 refresh derived data 或重匯既有專利。

## Acceptance Gate

以一個固定 analysis snapshot、使用者選定圖表與最大目標完成真實 CLI run；證明全部選圖都進 payload 且出現在 PPT、CLI 可唯讀補證據、所有數字可追溯、任何寫入／任意 SQL 均被工具層與 DB 層拒絕。最後產生完整 PPTX、manifest、evidence map、工具呼叫 audit、全頁 PNG、`PptQualityReport` 與必要時的 `RegenerationPlan`；quality gate 通過且使用者逐頁接受後才可 archive。
