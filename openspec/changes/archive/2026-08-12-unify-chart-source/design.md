# Design: 圖表單一來源（unify-chart-source）

## Context（2026-08-12 實碼盤點）

- `chart_profiles.PROFILES`＝{web, ppt}，`DEFAULT_PROFILE="ppt"`：**原檔名的 SVG
  是 PPT 尺寸**，web 版另存 `.web` 中綴檔；`render_sections_all_profiles` 跑兩輪。
- PPT 側消費者現況：`resolve_ppt_asset` **全庫零呼叫**（chart_bundle／build_ppt
  已刪）；`profile_manifest.json` 只有產生端沒有讀取端。皆為 2026-08-10 移除
  PPT 線後的殘留。
- web 側消費者：index.html 嵌圖走 `resolve_web_asset`（有 `.web` 用它、沒有退原檔）；
  CLI 解讀讀 sections 引用的**原檔名**。
- `version_meta.json` 有真消費者（main.py 版本歸屬），`artifact_manifest.json`
  是追溯來源——兩者**不在本 change 範圍**。

## Decisions

### 1. 單一 profile＝WEB 尺寸，寫入原檔名

不是「把 web 檔改名」，是**翻轉預設**：唯一一輪渲染用 WEB sizing
（`chart_sizing.WEB`：15px 資料字級、96dpi、`chart_scale()`≡1.0），輸出到既有
原檔名。理由：原檔名是 `report_data.json` sections、artifact_manifest、CLI prompt
引用的名字——動檔名會波及所有引用端；動**內容尺寸**則只影響顯示，且 web 尺寸
正是網頁與（會自行 refit 的）deck 需要的。

### 2. 舊版本相容靠 `resolve_web_asset` 現有語意，零遷移

| 版本 | 原檔名內容 | `.web.svg` | resolve_web_asset 結果 |
|---|---|---|---|
| 舊（本 change 前） | PPT 尺寸 | 有 | 用 `.web.svg` ✓ |
| 新 | **WEB 尺寸** | 無 | 退原檔 ✓（就是 web 尺寸） |

兩種版本都顯示正確，不重產、不搬檔、不加旗標。函式與其測試原樣保留。

### 3. 退場清單（全部是移除，不是改寫）

- `chart_profiles`：PPT profile 項、`profile_context` 對外用途、
  `resolve_ppt_asset`、`parse_profile_filename` 的 ppt 分支、`profile_filename`
  的中綴邏輯（單 profile 後恆回原名）。模組可縮到「sizing 轉發＋resolve_web_asset」。
- `chart_runner`：`render_sections_all_profiles` 第二輪（含 sections 還原技巧）、
  `build_profile_manifest`＋`PROFILE_MANIFEST_NAME` 與其上傳項。
- `chart_sizing.PPT` 常數：**保留定義並加註退役**——`chart_scale` 歷史比值、
  deck skill 文件引用它說明幾何；只斷渲染端引用，不刪知識。

### 4. 幾何預設翻轉的測試面

預設尺寸 PPT→WEB 會讓斷言絕對尺寸／字級的圖表測試紅（2026-08-07 反向實驗
實測 13 支）。處理原則照專案規則：**逐支更新並註記契約為何改**（單一來源定案、
日期、本 change id），不得刪測試了事。守門新增：「每張圖恰一檔」與
「不產 profile_manifest」兩條不復活契約。

### 5. deck skill intake（agent 端，隨附）

新 `assemble_from_version.py`：輸入＝版本目錄路徑或版本名（本機
`var/report_cache/` 優先、缺檔走既有 asset 端點按需拉到暫存 work 目錄），
輸出＝既有中間格式 `work/report.json`＋`work/charts/*.svg`——
`texts` 改取自 `narratives.json`、`tables`/`patent_ids` 取自 `report_data.json`
rows、`notes` 取自 encoding_notes／reader_guide、`report_meta` 取自
`version_meta.json`。`extract_report.py` 降為「拿到外來 HTML 單檔」的 fallback。
第 2 步（plan_deck）以後零改動；改完跑 skill 自帶 `check_docs.py`＋`regression.py`。

## Test Strategy

- 契約：單檔輸出（掃 run_dir 無 `.web.svg`）、無 profile_manifest、
  chart_profiles 縮編後的公開面、resolve_web_asset 新舊版本雙路徑。
- 幾何：既有 sizing 測試逐支改斷 WEB 值（附註記）。
- 實物：產一版新報表（網頁逐卡看、CLI 解讀一輪）＋deck skill 對新版本跑
  regression 像素比對。

## Risks

- 漏掉某個仍讀 PPT 尺寸的隱性消費者 → 動工第一步是語意全庫搜
  （`profiles`／`.web`／`chart_scale`／尺寸常數），不是只搜函式名。
- 舊版本混新版本同屏比較時字級觀感不同（舊卡 15px web 檔、新卡 15px 原檔）
  ——同為 15px，無實際差異；列入實物驗收確認。
