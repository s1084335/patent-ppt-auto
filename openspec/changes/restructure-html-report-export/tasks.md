# Tasks — restructure-html-report-export

分支：`feat/restructure-html-report-export`。對象＝`chart_runner.render_index`
產的版本目錄 `index.html`（**不是**前端預覽頁）。前端 `exportReportHtmlFile` 不改。

## 1. TDD：HTML 結構改版

- [ ] 1.1 Red：`render_index` 產出契約——導覽項數＝章節數且 `href="#sec-{report_key}"`
      對得上；每章 DOM 順序為 **解讀 → 圖 → 數據表**；`<h1>` 非寫死 `Patent Report`；
      數據表預設列數 5；無外部資源引用（離線可開）
- [ ] 1.2 Green：改 `render_index` 的區塊組裝與 CSS——
      ① 頂部 sticky 導覽（純 CSS＋錨點，`scroll-margin-top` 防遮）
      ② 解讀移到圖之前（⚠ 解讀是**逐變體**掛在 chart-panel 內，隨切換鈕一起換）
      ③ `.chart-media` 固定 `height:400px; width:auto`＋`.zoom` 展開原尺寸
      ④ `<details>` 預設列數 20→5，summary 改「共 N 列，預設顯示前 5 列」
      ⑤ `<h1>` 取 `meta` 的 workspace 名稱，缺值退回既有標題
- [ ] 1.3 點擊放大 JS 併入既有內嵌 script（toggle／expand 同一段）；
      ⚠ 不得引入任何外部依賴——單檔離線可用是硬需求

## 2. TDD：`chart_sizing.PPT` 刪除

- [ ] 2.1 Red：契約改為「`WEB` 是唯一 profile」——`chart_sizing` 不再匯出 `PPT`；
      `test_chart_sizing_profile` 三支比對 WEB/PPT 差異的斷言改寫（**寫明契約為何改**）
- [ ] 2.2 Green：刪 `PPT` 常數；`ChartSizing` 移除只有 PPT 在用的三欄
      （`hero_frame_in`／`wide_frame_in`／`wide_aspect_min`）；
      確認 `WEB` 各欄值**一字不變**（本 change 不動產圖尺寸）
- [ ] 2.3 檢查殘留引用歸零（含測試與註解中的誤導性描述）

## 3. 組合驗收

- [ ] 3.1 OpenSpec strict、目標測試、範圍回歸
      （`-k "chart_sizing or render_index or index_html or export"`）
- [ ] 3.2 實物驗收（Playwright，1600×1000，證據入 `output/_verify/`）：
      同一版本 `report_trial_20260812_133901` 改版前 8080px → **後 ≤6500px**；
      導覽逐項跳轉落位正確；圖預設 400px、點擊後原尺寸；表預設 5 列、展開後列數正確
- [ ] 3.3 相容：三個時代的版本目錄各跑一次 `--reindex` 重渲染，確認不破
      （含 `.web.svg` 時代與前 web 時代）
- [ ] 3.4 自包單檔：走前端「匯出 HTML 檔」下載一份，確認**離線可開**、
      SVG 全為 data URI、無外部資源引用
- [ ] 3.5 揭露未覆蓋（既有版本目錄不自動重產＝刻意取捨）；使用者接受後 archive
