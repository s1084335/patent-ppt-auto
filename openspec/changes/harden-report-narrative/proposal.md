# Proposal: 報表解讀的可信度與人工介入（harden-report-narrative）

## Intent

讓交付給使用者的那份解讀**可信**：來源查得到、內容過得了關、人改得動。

⚠ 本 change 原名 `enforce-narrative-evidence-footprint`，只涵蓋「取證足跡」一件。
2026-08-21 併入品質鎖覆蓋、產出前自檢與人工編輯後改為現名——名字涵蓋不了範圍，
下一個讀它的人就會找錯地方。

## Why

PPT 與 deck 兩條交付線先後停產後，**HTML 報表是唯一交付物**。而它的解讀目前
三個層次都沒有把關：

### 一、來源：取證從未發生，而且看起來完全正常

2026-08-20 job `ai:narrative #452` 實測：

| 應該留下的痕跡 | 實際 |
|---|---|
| MCP 取證工具呼叫 | **0 次**（35 次工具呼叫全是 Read／Grep／Glob／Write） |
| `narratives.json` 頂層 `evidence` | **欄位不存在** |
| job result 的 `query_audit`／`query_count`／`contract_warnings` | **欄位不存在** |

對外表現是 `succeeded`、`17/17 變體`、`pending=[]`、零警告——從任何介面看都是完美通過。
17 個變體的判讀全部只依 `report_data.json` 的聚合數字與 SVG 上的文字標籤寫成。

⚠ 它 03:29 **讀過 `data_access.md`**（取證地圖）才決定不做。不是不知道有這條路。

破口不在模型不聽話，而在**四層護欄全部只驗字面、沒有一層驗行為**（逐層見 design）。

### 二、內容：品質鎖鎖錯了對象

`validate_narrative_contract` 的鎖一到鎖九（字數、標點、數字一致、填充詞、具名、成因）
**全部作用在 `points`**。而 HTML 報表顯示的是長文 `text`——實測使用者匯出的
`割草機.html`：**17 個變體全是長文、headline 0 個、points 0 條**。

長文只被拿來當「points 的數字有沒有對得上」的參照，本身沒有任何檢查。

⚠ deck 線在時，至少 `check_content.py` 是硬閘門。deck 退場後，這個錯位的後果從
「PPT 兩層、HTML 一層」變成 **交付物零層**。

### 三、人工介入：顯示端做好了，入口被拔掉

`index.html:4927` 的解讀區本來就是「AI 原稿（`ai_original`）與人工修改（`manual_text`）
分欄，顯示以人工稿優先」的設計，連「（已人工修改）」標記都寫好了。

但 `manual_text` 讀的是 `view.edits.narratives`，而 `edits` 是**匯出工作台的
localStorage 結構**，已隨工作台移除——現在是恆為 `undefined` 的死路徑。

⇒ 使用者看得到 AI 寫錯，但改不動。

## Scope

依三支柱組織，每支柱可獨立驗收：

### 支柱一 · 來源可信

1. `evidence` 由選填升為解讀輸出契約的必要欄位；派工提示詞的形狀宣告必須與流程文件
   一致，不得再出現「文件要求／契約沒有」的矛盾。
2. 缺少 `evidence`、`evidence` 為空、或本次查詢數為零時產生契約警告。
3. `query_audit`、`query_count`、`contract_warnings` 隨工作結果寫入 `workflow_outputs`。
4. 前端 AI 任務卡顯示 `contract_warnings`。

### 支柱二 · 內容可信

5. **既有品質鎖的檢查對象擴及交付物實際顯示的文字**（長文），不只 `points`。
6. **產出前自檢**：檢查在 CLI 交件前執行，未過則把具體問題餵回 CLI 修稿，
   有輪數上限；輪數用盡仍未過時如實記錄，不無限重試。
7. **給 CLI 的內部指示不得洩漏進解讀文字**——寫作限制、字數上限、契約欄位名、
   工具規則都是作業指示，不是給讀者看的內容。

### 支柱三 · 人可介入

8. 解讀可**人工編輯並儲存**，逐報表、逐變體獨立。
9. 人工稿與 AI 原稿分欄保存於 `narratives.json`，顯示以人工稿優先（沿用既有顯示邏輯）。

### 貫穿全部

10. **守門改為行為驗證**：字串比對測試換成端到端測試，斷言整條鏈真的帶得到欄位。

## Non-goals

- **不把零取證升級為 job 失敗**。自檢迴圈在 job **內部**修稿；輪數用盡仍未過時
  `ai:narrative` 仍回 `succeeded` 並帶警告。是否升級為硬性失敗，等累積數次實跑分布
  後另案裁決。
- **不評判取證的深度**（查得夠不夠深、引用得對不對）。本 change 確保「查了沒有」與
  「查了什麼」成為事實；語意深度留人工驗收。
  ⚠ 這與支柱二不同：支柱二檢查的是**既有鎖的既有判準**是否覆蓋到交付顯示的文字。
- **不新增任何固定欄位名或固定句型**（見 Confirmed Decisions 5）。
- 不改變取證工具清單或 `query_database` 的 workspace 範圍守門（屬已完成的
  `scope-narrative-evidence-to-workspace`）。
- **匯出 HTML 檔不含編輯功能**——它是靜態交付物，但內容須包含人工稿。
- 不改報表引擎、圖表、分群、DB schema。
- 不重跑或修補 #452 的既有產出。
- 不做章節結構與版面改版（另案 `restructure-report-sections`）。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `report-export`：解讀的取證足跡、稽核落庫、警告可見、交付前自檢、品質檢查覆蓋範圍、
  判準性質、固定文案穩定性、人工編輯與保存、逐報表獨立。

⚠ `ai:narrative` 的 runner 行為改動一律歸在 `report-export`（既有 EXP-009 已把
job result 落庫定義在此）。**不另開 `ai-companion` delta**——同一份知識分兩處會漂移。

## Impact

| 面向 | 影響 |
|---|---|
| worker | `build_prompt` 形狀宣告；`validate_narrative_contract` 擴及長文與 evidence；新增自檢迴圈；`handle_ai_narrative` 結果白名單 |
| 提示詞 | `report-narrative-flow.md` 的 `evidence` 由選填改必要 |
| 前端 | AI 任務卡顯示契約警告；解讀區新增編輯與儲存入口 |
| API | 新增人工稿保存端點 |
| 測試 | `test_narrative_requires_research.py` 由字串比對改行為驗證 |
| DB | **不需要 migration**。三欄存進既有 `workflow_outputs.data_json`（JSONB） |

### Migration／資料搬移／rollback

- **migration**：無。
- **資料搬移**：無。既有 `narratives.json` 不回填 `evidence`；缺少時只產生警告。
- **derived refresh**：無。
- **重匯**：無。
- **rollback**：九項改動彼此獨立可逐項 revert；無 schema 變更，revert 後多出的欄位
  由消費端忽略。前端為靜態檔，重新載入即生效。

## Activation

合併後對**新派工的** `ai:narrative` 立即生效。既有已完成的 job 結果不追溯補欄位。
人工編輯功能合併後即可用。

## Confirmed Decisions

| # | 決策 | 來源 |
|---|---|---|
| 1 | 零取證只出警告，job 仍 `succeeded` | 原立案 |
| 2 | 不重跑 #452 的既有產出 | 原立案 |
| 3 | HTML 長文納入品質鎖 | 使用者 2026-08-20 |
| 4 | **產出前自檢**：檢查在 CLI 交件前跑，未過餵回修稿 | 使用者 2026-08-21 |
| 5 | 🔴 **不得重蹈第一世代 PPT 的錯**（三條硬規，見 design） | 使用者 2026-08-21 |
| 6 | 解讀可人工編輯儲存，**逐報表獨立** | 使用者 2026-08-21 |
| 7 | 人工稿存 `narratives.json`，與 AI 原稿分欄 | 使用者 2026-08-21 |
| 8 | 匯出 HTML 檔不含編輯功能，但內容含人工稿 | 使用者 2026-08-21 |
| 9 | 自檢形式：runner 跑檢查、錯誤餵回 CLI，**不給 CLI Bash、不擴權** | 規劃決定（見 design 形式比較） |
| 10 | 給 CLI 的內部指示**不得洩漏**進解讀文字 | 使用者 2026-08-21 |

## Open Questions

無。

## Acceptance Gate

動工前先記錄測試基線。逐項驗收，未執行與不適用要分開揭露。

### 支柱一 · 來源

| # | 判準 | 怎麼驗 |
|---|---|---|
| 1 | 派工提示詞形狀宣告含 `evidence`，與流程文件無矛盾 | 逐字比對兩處 |
| 2 | 無 `evidence` 時產生可辨識警告，且 job 仍 `succeeded` | 實跑 |
| 3 | 三欄落庫；零查詢時為 `0`／`[]`／`[]`，**不得省略欄位** | 查 `workflow_outputs` |
| 4 | AI 任務卡**實際顯示**該警告 | 實機檢視，不接受「應該會顯示」 |

### 支柱二 · 內容

| # | 判準 | 怎麼驗 |
|---|---|---|
| 5 | 品質鎖對**長文**生效 | 餵一段違反鎖四（零數字）的長文，須產生警告 |
| 6 | 自檢在交件前執行 | 實跑：CLI 首輪違規 → 收到具體問題 → 修正後才交件 |
| 7 | 輪數上限生效且用盡時如實記錄 | 人為讓檢查恆不過，確認達上限即停並留紀錄 |
| 8 | 🔴 **閘門未引入任何固定欄位名或固定句型** | 逐條檢視新增的檢查邏輯；任一條以「有無某小標」為判準即不合格 |
| 8b | 解讀文字**未覆述**字數／格式／契約／工具等內部指示 | 逐則檢視全部變體，不抽樣 |

### 支柱三 · 人工介入

| # | 判準 | 怎麼驗 |
|---|---|---|
| 9 | 可編輯並儲存，重新載入後仍在 | 實機 |
| 10 | 逐報表獨立：重跑 A 不影響 B 的人工稿 | 實跑：對 A 重跑解讀，確認 B 的人工稿不變 |
| 11 | 顯示以人工稿優先，並標示已修改 | 實機 |
| 12 | 匯出 HTML **含人工稿內容、不含編輯功能** | 匯出檔逐項檢視 |
| 13 | 未誤觸既有守門 | `test_export_edit_mode_removed` 仍綠（新入口不得沿用 `export-edit-toggle`／`toggleExportEditMode` 命名） |

### 貫穿

| # | 判準 | 怎麼驗 |
|---|---|---|
| 14 | **行為守門的真 Red**：人為移除 handler 白名單任一欄時測試變紅 | 實際證明它會紅，不是「有測試」 |
| 15 | 回歸 | 直接＋整合＋契約三層指名檔案執行 |
| 16 | OpenSpec strict | 通過 |
