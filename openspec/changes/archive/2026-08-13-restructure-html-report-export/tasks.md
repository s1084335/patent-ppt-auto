# Tasks — restructure-html-report-export

分支：`feat/restructure-html-report-export`。對象＝`chart_runner.render_index`
產的版本目錄 `index.html`（**不是**前端預覽頁）。前端 `exportReportHtmlFile` 不改。

## 1. TDD：HTML 結構改版

- [x] 1.1 Red：`render_index` 產出契約——導覽項數＝章節數且 `href="#sec-{report_key}"`
      對得上；每章 DOM 順序為 **圖 → 數據表 → 解讀**（使用者定案改序）；
      `<h1>` 非寫死 `Patent Report`；數據表預設列數 5；無外部資源引用（離線可開）
- [x] 1.2 Green：改 `render_index` 的區塊組裝與 CSS——
      ① 頂部 sticky 導覽（錨點；偏移量**動態**綁 `--nav-h`，見 1.4）
      ② 解讀移到數據表**之後**，離開 chart-panel 後帶 `data-group` 隨切換鈕連動
      ③ `.chart-media` **限寬** `max-width:860px; height:auto`＋`.zoom` 展開原尺寸
      ④ 數據表預設 5 列、第 6–20 列 `folded` 可展開（上限仍 20，07-21 定案不變）
      ⑤ `<h1>` 取 workspace 名稱；抬頭改「產製於 X · 版本」，產製參數降為次要行
- [x] 1.3 點擊放大 JS 併入既有內嵌 script（toggle／expand 同一段）；
      無任何外部依賴（單檔離線可用是硬需求）
- [x] 1.4 🔴 **修 first pass 的兩個自造缺陷**（實物驗收才現形）：
      ① `scroll-margin-top` 原只斷言「html 內含此字串」，抓到 CSS **註解**而假綠
      ——實際規則沒寫，跳轉後標題被導覽整個蓋住；斷言改綁 `.report-section` 規則內。
      ② 導覽字級調 16px 後 chip 換行、導覽列 42→102px，寫死 56px 偏移再度失效
      ——改 JS 量實際高度寫進 `--nav-h`＋監聽 resize（任何寫死值都會在某寬度失效）。
- [x] 1.5 字級（使用者指定）：正文與表格 **14px**、章節導覽 **16px**、圖內字 **11px**。
      ⚠ 圖內字由 `.chart-media` 寬度反推（15.1 × 860 ÷ 1180 ≈ 11.0px）——
      **寬度是圖內字級的唯一旋鈕**，不得去改 SVG（會連 deck 一起壞）。
- [x] 1.6 🔴 **修扁圖被拉伸放大**（實測）：`height:340px` 配 `max-width:100%`
      對 1180×210 的 IPC L4 兩條同時觸發 → 1490×340（比例 5.62:1 壓成 4.38:1、
      放大 26%、圖內字 19.1px 反而大於正文）。改限寬不限高，所有圖縮放比一致。
- [x] 1.7 外觀設計（使用者「淺色系為主」）：色票沿用產品前端
      （brand `#0F3460`／`#1A6BC4`、paper `#F4F6F9`、wash `#EDF2F9`），
      `color-scheme: light` 明確單一主題；字型堆疊 `Noto Sans TC` 排第一（對齊 deck 定案）；
      章節標題左側 brand 短條、表格只留橫線不圍格、解讀區左線＋淺底；加 `@media print`。
- [x] 1.8 🔴 **主題統計表依通道分段**（既有缺陷，07-21 定案只實作一半）：
      `source_field` 欄早已隱藏、列卻從沒分過——欄藏了反而讓讀者失去線索。
      新增 `_segmented_table_html`（通道名取自既有 `SOURCE_SEGMENT_LABELS`）。
- [x] 1.9 🔴 **數據表跟著變體切換**（使用者：「技術統計表看技術就好」）：
      1.8 的分段只是半套——切換鈕仍對表格無效。變體產出時**自帶 `source_field`**
      （原本 `for index, (_, ...)` 把它丟掉，下游只能猜 `variant_key`）；
      新增 `_variant_table_rows`，逐變體出 `.data-panel` 由 toggle JS 一併切。
      ⚠ 只在「變體真能區分資料」時逐變體出表——IPC 的 L4／L5 共用同一份明細。

## 2. TDD：`chart_sizing.PPT` 刪除

- [x] 2.1 Red：契約改為「`WEB` 是唯一 profile」——`chart_sizing` 不再匯出 `PPT`；
      `test_chart_sizing_profile` 三支比對 WEB/PPT 差異的斷言改寫（**寫明契約為何改**）
- [x] 2.2 Green：刪 `PPT` 常數；`ChartSizing` 移除只有 PPT 在用的三欄
      （`hero_frame_in`／`wide_frame_in`／`wide_aspect_min`）；
      `WEB` 各欄值**一字未變**（本 change 不動產圖尺寸）
- [x] 2.3 殘留引用歸零：`chart_runner` 三個對應常數一併刪除，測試補
      `test_removed_ppt_frame_constants_stay_gone` 防復活

## 2b. TDD：申請人年度矩陣改跨度圖（design 7.8b，本 change 新增範圍）

> 原掛在 deck change 的 tasks 3b.4b。實作前發現它**直接改變 HTML 報表**
> （單一來源之下引擎改動兩端同步），與本 change 驗的是同一個畫面——
> 掛 deck 線會讓 HTML 這邊要等 deck 做完才變。使用者裁決移來本 change。

- [x] 2b.1 Red：跨度條幾何契約（起訖對得上資料年份、單點列畫成方塊、
      條末標總件數、20 列進單一畫布、依總量排序、`_more` 檔退場）
- [x] 2b.2 Green：`render_year_span_chart`；`_build_applicant_year_matrix_section`
      改用它並**併成一張**（Top 10 與第 11–20 名）；`CHART_FILE_REPORTS` 移除
      `_more.svg` 登記；主題演進**維持泡泡**（跨度平均佔全軸 56%，畫成條會糊成等長）
- [x] 2b.3 🔴 **防失真**：本專案填格率僅約 11%（曾晴 2020／2022／2024），
      純甘特條會把斷續投入畫成連續布局——條上以圓點標出**實際有件的年份**
- [x] 2b.4 `test_chart_sections` 五支隨契約更新，逐支寫明「變的是圖元與檔案數，
      不變量（圖只截前 20、資料仍保留 22 列、年份截斷要標明）照守」

## 3. 組合驗收

- [x] 3.1 目標測試與範圍回歸：`test_html_report_structure`（20 支）、
      `test_year_span_chart`（8 支）、`test_chart_sections`、`test_chart_sizing_profile`、
      `test_report_version_follows_expand`、`test_api_frontend` 全綠；
      範圍回歸 324 passed。⚠ 未執行 OpenSpec CLI strict（環境無 `openspec` 指令，
      與前兩次 archive 同）——改以四份文件齊備與 delta 格式人工檢查代替。
      ⚠ 既有債三支紅（`test_launcher_and_companion_status`）屬列管的 launcher 11 紅，
      非本次造成。
- [x] 3.2 實物驗收（Playwright，1600×1000，證據入 `output/_verify/html_restructure/`）：
      8080px → **7524px**；導覽 9 項、跳轉 y=110 未被遮（1600／1200 兩寬度皆驗）；
      圖內字 **11.0px** 全圖一致、無變形；表預設 5 列可展開；
      主題統計表隨變體切換（技術 6 列／功效 9 列）；跨度圖 20 條／31 標點／20 總計
- [x] 3.3 相容：三個時代版本目錄各跑一次 `--refresh-index`——
      `.web.svg` 時代／單一來源／前 web 時代（模擬移除 `.web.svg`）
      皆 exit 0、導覽 9 項、引用圖 14 張、**缺檔 0**
- [x] 3.4 自包單檔：走前端「匯出 HTML 檔」下載（139 KB）——**外部資源引用 0**、
      仍指向 `.svg` 的 src 0、內嵌 data URI 14 個；斷網開啟 9 章可讀、無 pageerror。
      ⚠ 版面驗證另走本機重建檔：匯出讀的是 **DB artifact**，手動 CLI 不上傳（design §7）
- [ ] 3.5 揭露未覆蓋；使用者接受後 archive

### 未覆蓋與已知限制（3.5 揭露）

- **既有版本目錄不自動重產**：使用者要新版面須重產報表或重產解讀
  （兩者都會 `upload_run_dir`）；手動 `--refresh-index` 只改本機檔，匯出仍是舊版。
- **未跑 OpenSpec CLI strict**：環境無該指令（前兩次 archive 亦同）。
- **未在真實 DB 上驗匯出新版面**：需重產報表（要 DB），本機無 postgres；
  以「本機重建檔驗版面 ＋ 匯出流程驗 data URI／離線」兩段替代。
- **圖置中後兩側留白**約各 300px：限寬縮圖的必然，待使用者實物感受後決定是否調整。
- 三支 launcher 測試紅屬既有債（列管中），未在本 change 處理。
