# report-export（delta）

## MODIFIED Requirements

### Requirement: 解讀完成的 HTML 報表為交付物

系統 SHALL 以「報表產製 → AI 解讀 → 解讀嵌入 `index.html`」為交付主線；
報表種類頁版本區 SHALL 提供「匯出 HTML 檔」入口，產出**自包單檔**
（SVG 內嵌 data URI，離線可開）。

該交付檔 SHALL 以**章節式**呈現：頂部提供章節導覽（章節名＝報表 section 標題，
不另建第二份對照），每章依序為**解讀 → 圖表 → 數據表**；圖表 SHALL 以縮圖
呈現並可展開原尺寸，數據表 SHALL 預設收合為前數列。

#### Scenario: 從報表種類頁匯出自包 HTML

- **WHEN** 使用者於版本區按「匯出 HTML 檔」
- **THEN** SHALL 下載該版本的單一 `.html` 檔
- **AND** 檔內所有圖表 SHALL 為內嵌 data URI，無外部資源引用
- **AND** 已產出的 AI 解讀 SHALL 隨章節呈現；未產出時標示待產生

#### Scenario: 讀者定位到特定章節

- **WHEN** 讀者開啟交付檔
- **THEN** 頂部 SHALL 有涵蓋全部章節的導覽，項數等於章節數
- **AND** 點選任一項 SHALL 跳至該章，且章節標題不被固定導覽遮住
- **AND** 導覽 SHALL 不依賴外部資源（離線可用）

#### Scenario: 圖表為證據而非主角

- **WHEN** 章節含圖表
- **THEN** 圖表 SHALL 以固定高度縮圖呈現，使圖內文字小於正文
- **AND** 讀者 SHALL 能就地展開原尺寸檢視細節
- **AND** 縮放 SHALL 等比、不得裁切圖面

#### Scenario: 數據表預設不佔版面

- **WHEN** 章節含數據表
- **THEN** SHALL 預設只顯示前數列並標示總列數
- **AND** 展開後 SHALL 顯示全部列（合計列行為不變）

#### Scenario: 解讀契約檔隨 backend 部署

- **WHEN** `ai:narrative` 於任何部署環境執行
- **THEN** 解讀契約（flow／content_standard 節錄／data_access）SHALL 自
  `backend/app/worker/prompts/` 載入（可用 `REPORT_NARRATIVE_FLOW_PATH` 覆寫）
- **AND** 不得依賴已移除的 `skills/patent-report-ppt/`
