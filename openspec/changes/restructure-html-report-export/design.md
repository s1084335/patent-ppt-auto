# Design: 匯出 HTML 報表章節式改版（restructure-html-report-export）

## Context

### 改版對象的正確定位（2026-08-12 實查，先前一度誤判）

```
chart_runner.render_index(path, sections, meta)   ← 本 change 的唯一戰場
  → 版本目錄/index.html（引用 *.svg）
      → 前端 exportReportHtmlFile()：把 src="*.svg" 換成 data URI，下載單檔
```

⚠ 匯出檔**不是前端組的**。前端只做 data URI 內嵌那一步，所以改版落點在後端
Python 的 HTML 產生器。前端預覽頁（`#report-inline-view`）是**另一套渲染**，
屬工作介面，不在本 change。

`render_index` 有兩個呼叫點：正常產製、以及「解讀回填後重渲染」（`--reindex`）
——兩者共用同一函式，改一處兩邊生效。

### 現況結構（`chart_runner.py:2780` 起）

```html
<h1>Patent Report</h1>              ← 寫死英文
<p class="meta-bar">…</p>
<section class="report-section">    ← 每章一張卡，×9
  <div class="section-head"><h2>標題</h2><links></div>
  <p class="section-note">…</p>
  <div class="card-data">數據表（前 20 列＋合計，可展開）</div>
  <div class="toggle-bar">L4／L5 切換</div>       ← 多變體章節才有
  <div class="chart-stage">
    <div class="chart-panel"><img class="chart-media">…
      <div class="explanation">解讀</div>          ← 解讀在圖之後、卡片最底
    </div>
  </div>
</section>
```

實測（1600×1000）：總高 8080px；`.chart-media { max-width:100%; height:auto }`
在 1180px 圖 ＋ 更寬容器下＝**原尺寸顯示**；字級 body 16／h1 28／h2 19／表 15／
註記 13px；圖內字 15.1px。

## Decisions

### 1. 章節導覽＝現有 section 標題，錨點跳轉

- 導覽項**不另取名**：直接用 `section["title"]`（與檢視選單、PPT 章節同源）。
  ⚠ 不建第二份章節名對照——那是「同一份知識兩處落點」。
- `id="sec-{report_key}"`＋`scroll-margin-top` 讓 sticky 導覽不遮標題。
- 導覽列 sticky 於頂；**純 CSS＋錨點**，不需 JS（單檔離線可用是硬需求）。

### 2. 版面順序改為 圖 → 數據表 → 解讀（2026-08-12 使用者定案）

現行是「表 → 圖 → 解讀」——一進章節先撞到一大片數字。改為圖先給印象、
表提供佐證、解讀作結。**改動＝把數據表從章節最前移到圖與解讀之間。**

🔴 **解讀要離開 chart-panel，但必須保住連動**：解讀目前掛在每個 `chart-panel`
**內**（逐變體，IPC 的 L4／L5 各有各的解讀），靠 panel 的 `hidden` 一起切換。
移到表之後就離開了 panel，若不處理，切到 L5 會讀著 L4 的解讀——**靜默錯配，
畫面不會有任何異狀**。

做法：解讀區自帶同一組 `data-group`／`id="{group}-{i}-exp"`，既有 toggle JS
一併切換（現行只切 `.chart-panel[id^="group-"]`，擴充成也切解讀）。
單變體章節就是一段文字接在表後。無解讀時維持既有 pending 標示。

### 3. 圖固定 400px 高 ＋ 點擊看原尺寸

```css
.chart-media { height: 400px; width: auto; max-width: 100%; cursor: zoom-in; }
.chart-media.zoom { height: auto; }
```

- 400px 是**下限**：SVG 縮放時字等比縮小，400px 時圖內字 15.1 × (400/560) ≈ **10.7px**，
  比正文 16px 小一級——圖才會退成證據。再小會破可讀性。
- 點擊切換需要一行 JS；⚠ 單檔離線可用，故 JS 必須**內嵌且無外部依賴**
  （與既有 toggle／expand 的 JS 同一段，不引入任何函式庫）。
- ⚠ `viewBox` 不可省（既有教訓：沒有它，`max-width` 會裁掉右側與下方而非等比縮）。

### 4. 數據表預設前 5 列

`_data_table_html` 現行硬編 20 列且**刻意不給展開**（2026-07-21 定案
「不讓人看百筆數據」）。改為：**預設顯示 5 列，第 6–20 列收合可展開**，
summary 標示總列數；超過 20 列的既有行為（只註記「顯示前 20 列｜總列數 N」）
**不變**。

⚠ **展開上限仍是 20 列**——本次只改預設密度，不推翻 07-21 定案。
⚠ 這是本批**省高最多**的一項：申請人年度矩陣 21 列 697px → 約 190px。
合計列（`totals-cell`）永遠可見，不進收合區。

### 5. 標題用實際報表名

`<h1>` 由 `meta` 取 workspace 名稱（沿 deck 封面同一來源：`version_meta` 的
workspace 歸屬），缺值退回既有標題文字。⚠ 不新增查詢——`render_index` 已收 `meta`。

### 6. `chart_sizing.PPT` 刪除

- **理由**：`chart_runner` 只 `import WEB`；全庫無第二個消費者，只剩測試在比對
  兩者差異。其 `hero_frame_in`／`wide_frame_in` 是**已移除的 `build_ppt`** 的圖框，
  與 deck skill 的幾何（`CW=12.333in`、圖區高 4.68in）完全是兩套數字。
- 留著的風險是**假知識**：日後有人改 PPT profile 以為會影響簡報，其實不會。
- 連帶：`ChartSizing` dataclass 的 `hero_frame_in`／`wide_frame_in`／`wide_aspect_min`
  三欄一併移除（只有 PPT profile 在用）；`test_chart_sizing_profile` 三支比對
  WEB/PPT 差異的斷言改為「WEB 是唯一 profile」的契約。
- ⚠ 與 RPT-010（圖表單一來源）同向：那條已宣告「引擎不得為特定輸出媒介預先產
  第二份尺寸版本」，本次是把殘留的第二份**參數**也清掉。

## Test Strategy

- **HTML 結構契約**（純函式層，不需瀏覽器；`tests/test_html_report_structure.py`）：
  導覽項數＝章節數且錨點對得上、每章 DOM 順序為 **圖→表→解讀**、
  多變體章節的解讀帶同組 `data-group`（連動不錯配）、表格預設列數 5、
  `h1` 非寫死英文、無外部資源引用。
- **實物驗收**（Playwright，1600×1000）：總高 ≤6500px、錨點跳轉落位正確、
  圖預設高 400px 且點擊後為原尺寸、表展開後列數正確。
- **相容**：拿三個時代的版本目錄各重渲染一次（`--reindex`），確認不破。
- **回歸範圍**：`-k "chart_sizing or render_index or index_html or export"`。

## Risks

- **改的是共用渲染函式**：`--reindex`（解讀回填）與正常產製共用，任一路徑破了
  都會讓版本目錄的 index.html 壞掉。→ 契約測試涵蓋兩個呼叫點。
- **舊版本目錄不重產**：既有 8080px 的檔案不會自動變好；使用者要新版面得重產
  或走 `--reindex`。此為刻意取捨（不動既有產物），在驗收時明講。
- **點擊放大的 JS**：單檔離線是硬需求，JS 必須內嵌無依賴；若日後有人加外部庫，
  匯出檔會在離線環境失效——測試斷言「無外部資源引用」守住。
