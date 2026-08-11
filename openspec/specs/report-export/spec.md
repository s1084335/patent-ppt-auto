# Report Export Specification

## Purpose

定義從報表版本、AI 文案槽、deterministic 組版、真實 PPT 預覽到 artifact 下載的現行輸出契約。
## Requirements
### Requirement: EXP-001 AI 產內容、程式負責組版

系統 SHALL 只讓 AI 產生受契約約束的敘述／確認槽內容；頁面幾何、字級、圖表放置、數字與 `.pptx` 組裝由 deterministic 程式負責。

#### Scenario: AI 回傳版面指令

- **WHEN** AI 輸出包含任意排版建議
- **THEN** 組版器 SHALL 只消費契約允許的文字槽
- **AND** 不讓 AI 改動數字或頁面幾何

### Requirement: EXP-002 選定報表決定頁面

系統 SHALL 依實際選定且有資料的 report keys 建立頁面，支援技術／功效與 IPC/CPC 階層等 variant 分頁，並揭露選定但未渲染的報表。

#### Scenario: 選定報表無資料

- **WHEN** report key 被選定但資料為空
- **THEN** 匯出結果 SHALL 在 metadata 顯示 missing/skipped 原因
- **AND** 不產生看似正常的空白分析頁

### Requirement: EXP-003 Narrative 與頁面精確配對

系統 SHALL 優先以 report key、variant key 與圖檔 identity 配對敘述，拆頁後不得讓不同頁誤用同一段文字。

#### Scenario: 同一 report 有兩個 variant

- **WHEN** 技術與功效 variant 分成兩頁
- **THEN** 每頁 SHALL 取得自己的 narrative

### Requirement: EXP-004 真實 PPT 預覽閘門

系統 SHALL 以實際 `.pptx` 渲染結果提供頁面預覽，讓使用者在下載前檢視；分析頁與匯出頁維持不同視角。

#### Scenario: 使用者預覽版本

- **WHEN** 使用者選擇一個 PPT 版本
- **THEN** 匯出頁 SHALL 顯示該檔案的逐頁預覽
- **AND** 不以 HTML/CSS 模擬頁冒充真實 PPT

### Requirement: EXP-005 版本與 artifact 持久化

系統 SHALL 保存 `.pptx`、組版 metadata、必要文字資料與預覽來源，使 backend 可跨容器列出、下載及重新 materialize 指定版本。

#### Scenario: AI Companion 與 backend 位於不同機器

- **WHEN** Companion 產生 PPT
- **THEN** 必要 artifact SHALL 寫入共享 DB artifact store
- **AND** backend 不依賴 Companion 本機檔案路徑

#### Scenario: 瀏覽既有報表版本

- **WHEN** 使用者選擇一個既有報表版本
- **THEN** backend SHALL 提供該版本的結構化內容與 `.pptx` artifact 清單
- **AND** 前端 SHALL 顯示該版本的可下載 PPT 項目
- **AND** 版本清單不得為取得 metadata 而讀取全部報表內容或 blob

#### Scenario: 同一報表版本重產 PPT

- **WHEN** 使用者對同一報表版本再次產生 PPT
- **THEN** 系統 SHALL 使用下一個 `_rN` 版本保存新檔
- **AND** 舊 PPT artifact 仍可列出與下載

### Requirement: EXP-006 驗收產物可追溯

系統 SHALL 將本輪 PPT、轉圖與截圖放在 `output/_verify/<主題>/`，且交付前檢查全部受影響頁面。

#### Scenario: 版面變更影響多頁

- **WHEN** 修改共用版型或主題
- **THEN** 驗收 SHALL 涵蓋所有受影響頁
- **AND** 不以少數抽樣頁宣告全體通過

### Requirement: EXP-007 正式資料完整重產

系統 SHALL 以完成 migration、重匯與 derived refresh 的正式資料重產整份報告，並驗證 artifact 與全部受影響頁面。

#### Scenario: A5 正式交付

- **WHEN** 最小 DB gate 已通過並完成完整報告重產
- **THEN** 所有選定且有資料的報表 SHALL 出現在 report metadata 與 PPT
- **AND** `.pptx`、圖表、report data 與 narratives SHALL 可由 artifact store 重新讀取
- **AND** 全部受影響頁面 SHALL 完成逐頁檢視

### Requirement: 解讀完成的 HTML 報表為交付物

系統 SHALL 以「報表產製 → AI 解讀 → 解讀嵌入 `index.html`」為交付主線；
報表種類頁版本區 SHALL 提供「匯出 HTML 檔」入口，產出**自包單檔**
（SVG 內嵌 data URI，離線可開）。

#### Scenario: 從報表種類頁匯出自包 HTML

- **WHEN** 使用者於版本區按「匯出 HTML 檔」
- **THEN** SHALL 下載該版本的單一 `.html` 檔
- **AND** 檔內所有圖表 SHALL 為內嵌 data URI，無外部資源引用
- **AND** 已產出的 AI 解讀 SHALL 隨卡呈現；未產出時卡片標示待產生

#### Scenario: 解讀契約檔隨 backend 部署

- **WHEN** `ai:narrative` 於任何部署環境執行
- **THEN** 解讀契約（flow／content_standard 節錄／data_access）SHALL 自
  `backend/app/worker/prompts/` 載入（可用 `REPORT_NARRATIVE_FLOW_PATH` 覆寫）
- **AND** 不得依賴已移除的 `skills/patent-report-ppt/`

