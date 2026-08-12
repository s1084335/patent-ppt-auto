## Why

每張圖表現產兩個 SVG（PPT 版原檔名＋web 版 `.web` 中綴，14 張圖＝28 檔）。
PPT 版的消費者（`build_ppt`／`chart_bundle`）已隨 2026-08-10 PPT 交付線移除而消滅，
`resolve_ppt_asset` 與 `profile_manifest.json` 已是零消費者的死碼與死檔；
而唯一的未來簡報消費者（agent 端 `html-report-to-deck` skill）本來就逐圖重測字級，
不需要預放大的 PPT 版。雙 profile 只剩成本沒有收益。

2026-08-12 使用者定案：**HTML 與 PPT 共用同一套來源**（JSON＋單一 SVG），
各自流程在消費端適配——網頁原樣顯示，簡報端自行 refit 字級。

## What Changes

- **圖表輸出收斂為單一 profile**：以 WEB 尺寸（15px 字級、96dpi）渲染，
  寫入**既有原檔名**（不再產 `.web` 中綴檔）。每張圖一檔，28→14。
- **PPT 補償鏈退場**：`chart_profiles` 的 PPT profile、`profile_context` 雙輪
  渲染（`render_sections_all_profiles` 第二輪）、`chart_scale` 的 PPT 縮放補償、
  `resolve_ppt_asset`（死碼）、`profile_manifest.json`（死檔）。
- **`resolve_web_asset` 保留**：舊版本目錄仍是「原檔名＝PPT 尺寸＋`.web.svg`」，
  fallback 語意讓新舊版本都顯示正確的圖，不重產舊版本。
- **agent 端接軌（隨附工作，非產品 spec）**：`html-report-to-deck` skill 的
  intake 從「拆 HTML」改為「讀版本目錄／asset 端點按需拉」，中間格式
  （`report.json`＋`charts/`）與第 2 步之後全部不動。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `patent-reporting`：圖表輸出從雙 profile 收斂為單一來源（ADDED requirement）。

## Scope

`backend/app/reports/chart_profiles.py`、`chart_runner.py`（渲染迴圈、manifest、
上傳清單）、`chart_sizing.py`（PPT 常數的去留）、對應測試；
中央 skill `html-report-to-deck` 的 intake 腳本與 SKILL.md。

## Non-goals

- 不動 `report_data.json`／`narratives.json`／`version_meta.json`／
  `artifact_manifest.json` 的形狀（`version_meta` 有 main.py workspace 歸屬
  消費者、`artifact_manifest` 是追溯依據——盤點過，併檔收益低、動線多，不做）。
- 不重產既有報表版本；舊版本靠 `resolve_web_asset` fallback 照常顯示。
- 不新增打包格式（zip／單一大 JSON）——檔案數跟著消費者走，這次是移走死消費者。

## Decisions（已由使用者確認）

- 2026-08-12：「規劃這部分讓 PPT 和 HTML 從同套來源再去接到各自流程」——
  單一 SVG 來源＋消費端適配。
- 前置討論已確認：deck skill 逐圖重測字級（`fit_render_charts.py`），
  web 尺寸來源足夠；PPT 預放大屬產出端適配的遺產。

## 舊 change 處置

`separate-web-and-ppt-chart-profiles`（13/21）**作廢封存**：其 web profile 段
成果（`.web.svg`、index 嵌 web 版、`chart_sizing` WEB 常數）已上線且由本 change
承接為唯一 profile；其餘 8 項全屬 PPT 側，隨 PPT 線消滅。封存標頭註明由本
change 取代。

## Acceptance Gate

1. 新產版本：每張圖恰一個 SVG（原檔名、WEB 尺寸），無 `.web.svg`、無
   `profile_manifest.json`；index.html 嵌圖正確、字級 15px。
2. 舊版本（雙檔）網頁顯示不變（fallback 實測）。
3. CLI 解讀端到端一次（讀單一 SVG 產 narratives）。
4. deck skill 以新版本目錄跑通 intake→plan→fit→組版（regression.py 全綠）。
5. 範圍回歸；幾何預設值相關測試逐一以契約變更註記更新（預期紅一批，
   2026-08-07 曾實測 13 支）。
