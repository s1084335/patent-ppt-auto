# 專利報告 PPT 產製流程

> 同步來源：`D:\力山\.agents\context\export-report-flow-spec.md` v2.3（2026-07-30）。
> 本 skill 是「報告 → 匯出報告」工作台的 PPT 產製契約；若本檔與該規格衝突，以規格檔為準並先回報衝突。

## 執行 Runbook

### Runbook 就緒度

- 組版工具：`scripts/build_ppt.py` 已隨本 skill 目錄提供。
- 樣式來源：`theme.json` 為單一風格來源；第一版不做多模板／多風格。
- 第一版目標：讓 PPT 能穩定產出、可預覽、可下載、內容可用。
- 市場線狀態：**第一版暫停**。痛點交叉驗證與市場規模頁已從 PPT 大綱移除，不得再執行舊版 WebSearch 市場研究流程。

### 執行原則

- 引擎數字不可改寫；AI 只產敘事與 PPT 文案草稿。
- PPT 組版由 deterministic 程式執行，`build_ppt.py` 不呼叫 AI。
- 版型由使用者從固定清單挑；AI 不生成版型。
- 匯出報告工作台必須先預覽，再由使用者決定是否輸出；不得跳過預覽閘門。
- 缺漏不印在 PPT 內；第一版只在平台任務進度顯示。
- 沒有被選到、或 `report_data` 沒有資料的報表，不產生對應頁。
- 分群版本不一致、缺漏資料、無效 slot 名稱都只提醒，不阻擋第一版輸出。

### 觸發時機

- 使用者在「匯出報告」工作台要求產生或更新專利分析報告 PPT。
- 使用者選擇既有報表版本並要求預覽、產生或下載 PPT。
- 使用者要求重產單頁 PPT 文案與版型。

### 前置條件

1. 報表資料已由 Web 平台產出，可取得該版本的 `report_data.json`、圖表 artifact 與 `narratives.json`。
2. 若 `narratives.json` 尚未存在或不完整，平台需自動先跑 `ai:narrative`，完成後才接續 `ai:report_ppt`。
3. 讀資料時必須使用 `report_data.json` 與對應圖表 artifact；禁止只看圖寫結論，禁止繞過報表定義自行取數。
4. 若報表版本內有 `topic_run_id`／`topic_state_version`，匯出前可與目前 active 分群比對；不一致時提示，但不阻擋。

### 資料來源與產物

| 類型 | 檔案／來源 | 說明 |
|---|---|---|
| 報表資料 | `report_data.json` | 數字與章節資料唯一來源 |
| 解讀文案 | `narratives.json` | `ai:narrative` 逐報表、逐變體產生 |
| PPT 文案 | `approvals.json` slots | `ai:report_ppt` 綜合全量資料與 narratives 後產生 |
| 樣式 | `theme.json` | 字體、字級、顏色、座標的單一來源 |
| PPT | `<report_version>.pptx` / `<report_version>_rN.pptx` | 產出檔，不以市場線頁面為必要條件 |
| manifest | `<report_version>.manifest.json` | 檔案 hash、來源版本、缺漏資訊、追溯 metadata |

### PPT 流程 v2.3

```
① 使用者在報表種類選擇並產製報表
   → report_data.json／圖表 artifact
② narrative 產出
   → narratives.json
③ PPT slots 產出
   → AI 同時讀全部 report_data 與 narratives，產 approvals.json slots
④ deterministic 組版
   → build_ppt.py 依 theme.json 與 approvals.json 產 .pptx
⑤ 匯出報告頁預覽真實 .pptx
   → 使用者確認後下載或進一步重產單頁
```

### 章節組成：基礎 8 頁

| 頁 | kind | 標題 | report_keys | slots |
|---|---|---|---|---|
| 1 | `cover` | 專利情報整合分析 | `country_distribution`／`application_trend`／`lifecycle` | `cover.title` |
| 2 | `direction` | 研發方向建議 | 綜合本次實際包含的全部報表 | `direction.body` |
| 3 | `chart_with_narrative` | 申請趨勢 | `application_trend`／`publication_trend` | `trend.narrative` |
| 4 | `chart_with_narrative` | 技術分布 | `cluster_topic_table` | `tech.narrative` |
| 5 | `chart_with_narrative` | 競爭者佈局 | `applicant_country_distribution`／`applicant_ranking` | `competitor.narrative` |
| 6 | `chart_with_narrative` | 機會評估四象限 | `opportunity_quadrant` | `opportunity.narrative` |
| 7 | `table` | 附錄1：全分類技術指標總表 | `cluster_topic_table` | 無 |
| 8 | `table_with_narrative` | 附錄2：主要專利權人與申請人 | `applicant_ranking`／`owner_ranking` | `key_players.summary` |

已移除：

- 原 P7 痛點交叉驗證：`pain_point_quadrant`
- 原 P10 市場規模：`market.scope`／`market.size`
- 舊槽位：`pain_point.narrative`、`key_players.market`、`market.scope`、`market.size`

### 出頁規則

- `cover` 與 `direction` 恆出。
- 其他頁面：`spec.report_keys` 至少一個在 `report_data` 有資料才出頁。
- 多個 `report_keys` 的頁面只呈現實際有資料的 key；不得留空框、不得擺錯資料。
- 動態插頁同樣受 `report_data` 驅動；未選或無資料的報表不得出頁。
- 附錄插入點不得使用頁碼魔術數字；用 `PageSpec.is_appendix` 或等價顯式旗標表達。

### 封面統計卡

| 格 | 內容 | 來源 | 第一版規則 |
|---|---|---|---|
| 1 | 專利總數 | `application_trend` 加總 | 顯示 |
| 2 | 地域分布前 2 | `country_distribution` | 顯示 |
| 3 | 年份區間 | `application_trend` 年份 min–max | 顯示 |
| 4 | 未定 | 無 | 第一版不顯示／保留，不硬湊低價值指標 |

### 文案生成：兩階段

#### 階段 1：narrative

- 使用既有 `ai:narrative` 流程，逐報表、逐變體產生解讀，落 `narratives.json`。
- 解讀口徑仍遵守同目錄 `report-narrative-flow.md`。
- 解讀線維持「prompt 給路徑，CLI 自己讀目錄」；不改成資料檔路線。

#### 階段 2：PPT slots

- `ai:report_ppt` payload 必須包含 `report_data` 與 `narratives`。
- AI 產生扁平 slots，交給 runner 寫入 `approvals.json`。
- `direction.body` 必須綜合本次實際包含的全部報表，不得只看分群。
- 各頁文案要有頁間脈絡，但不得為製造脈絡而編造因果。
- 只能談 `report_data` 裡實際存在的報表；沒產的報表不得提及或憑常識補充。
- AI 回傳不存在於合法槽位清單的 slot 名稱時，自動過濾，並在平台任務進度提示；不直接 fail job。

合法槽位第一版為：

```json
{
  "report_version": "<報表版本，須與報表目錄一致>",
  "slots": {
    "cover.title": "封面標題（頁1）",
    "direction.body": "研發方向建議全文（頁2）",
    "trend.narrative": "申請趨勢解讀（頁3）",
    "tech.narrative": "技術分布解讀（頁4）",
    "competitor.narrative": "競爭者佈局解讀（頁5）",
    "opportunity.narrative": "機會四象限解讀（頁6）",
    "key_players.summary": "主要專利權人與申請人摘要（頁8）"
  },
  "layout_overrides": {},
  "position_overrides": {}
}
```

#### 文案 runtime 規則

逐 slot 文案規則與內容品質標準以 `report_ppt_content_rules.md` 為唯一 runtime 來源。`ai:report_ppt` payload 必須載入該檔內容，避免產品規格、skill 文件與 runner prompt 各自維護一份規則。

本段只保留產品契約：

- `SKILL.md` 定義匯出報告 PPT 的流程、產物、頁面與驗收。
- `report_ppt_content_rules.md` 定義文案產製服務實際使用的逐 slot 寫法與品質標準。
- `theme.json` 定義字體、顏色、座標與版面數值。
- `build_ppt.py` 負責 deterministic 組版，不呼叫文案產製服務。

`position_overrides` 只為相容舊資料保留清理路徑；v2.3 不做拖曳，不新增位置編輯。

### PPTX 組裝

本步驟由本目錄內建產生器執行，不呼叫 AI。

#### 產生確認槽範本

```
uv run --no-project --with python-pptx --with pymupdf --python 3.12 \
  python <skill 目錄>/scripts/build_ppt.py --init-approvals approvals.json
```

#### 產生 PPTX

```
uv run --no-project --with python-pptx --with pymupdf --python 3.12 \
  python <skill 目錄>/scripts/build_ppt.py \
    --report-dir <報表版本目錄> \
    --approvals approvals.json \
    --output-dir data/report_artifacts/ppt
```

行為契約：

- 輸入＝`report_data.json`／`narratives.json`／圖表 artifact／`approvals.json`／`theme.json`。
- `build_ppt.py` 只組版，不呼叫 AI，不改寫數字。
- 缺解讀、缺圖表、缺資料不印浮水印；寫入 manifest，平台任務進度顯示。
- 輸出 `.pptx` 與 manifest，manifest 需包含來源報表版本、來源目錄、hash、逐頁 `missing_slots`／`missing_reports`。
- 同版本重跑可以產 `<report_version>_r2.pptx`、`_r3.pptx`；歷史 PPT 檔案由平台列表呈現。

### 匯出報告工作台行為

- 工作台必須以真實 `.pptx` 渲染預覽；不要再用 CSS 模擬投影片作為正式預覽。
- 若版本已有 PPT，載入 `/reports/versions/{version}/ppt-files` 取得檔案列表，使用者可選檔預覽與下載。
- 若版本沒有 PPT，顯示「請先產生 PPT」並附「產生 PPT」按鈕。
- 按「產生 PPT」時，若 narrative 未產，系統自動先跑 narrative，再接續跑 PPT。
- 匯出報告下方要顯示歷史 PPT 檔案列表。
- 單頁 HTML 匯出若保留，樣式要跟 `theme.json` 對齊，不維持第三套寫死 CSS。

### 單頁重產

第一版等整份 PPT 可穩定產出後再做。

- 從目前預覽頁啟動「重產這頁」。
- 只傳該頁需要的 `slot_keys`，不得整份重跑 AI 文案。
- 可同時換版型。
- 候選版本名稱含頁面名稱＋時間，例如 `{page_slug}_{YYYYMMDD_HHMMSS}`。
- 使用者確認前只顯示候選結果，不覆蓋正式輸出。
- 確認時提供入口讓使用者選要覆蓋的目標檔案／版本。
- 第一版確認後直接覆蓋，不保留被覆蓋前版本。

### 五批實作與分批驗收

批次 0 測試路徑修復已完成，不算剩餘五批。剩餘工作必須五批做完，且每批完成後停止讓使用者驗收。

#### 批次 1：章節與結論

- 移除痛點頁與市場頁，PPT 基礎大綱改為 8 頁。
- P9 改名為「附錄2：主要專利權人與申請人」，並成為新第 8 頁。
- 選擇驅動出頁，只呈現有資料的 key。
- 封面補年份區間，第 4 格不硬湊。
- 結論頁可放圖表，且綜合本次實際包含的報表。
- payload 加 narratives，`_PPT_RULES` 補綜合、脈絡、護欄與只談真有的報表。
- manifest 接通 `missing_slots`／`missing_reports`，平台可取得缺漏。
- 本 skill 與 `build_ppt.py` 的槽位契約同步。

驗收：

- PPT 為 8 頁；痛點與市場頁不出現。
- 沒資料的報表不出頁。
- 缺漏只進平台提示，不印浮水印。
- 無效 slot 會被過濾並提示。

#### 批次 2：真實 PPT 預覽與產生入口

- 接入真實 `.pptx` 預覽。
- 沒 PPT 時顯示「產生 PPT」按鈕。
- 產生 PPT 會自動串 narrative → PPT。
- 實機驗收中文字型、表格、圖表、色塊。

#### 批次 3：歷史 PPT 與狀態提示收斂

- 匯出報告下方新增歷史 PPT 檔案列表。
- 接 `/reports/versions/{version}/ppt-files`。
- 任務進度列逐頁缺漏、無效 slot、分群版本不一致。
- 報表版本補 `topic_run_id`／`topic_state_version` 追溯；不一致只提醒、不阻擋。

#### 批次 4：編輯稿與 HTML 匯出收斂

- 編輯稿存 DB；能整合現有表／API 就整合，不行才新增。
- 跨裝置／跨瀏覽器可還原使用者編輯稿。
- 單頁 HTML 匯出樣式對齊 `theme.json`。

#### 批次 5：單頁重產

- 單頁重產只跑該頁 slots。
- 候選版本命名含頁面名稱＋時間。
- 使用者確認前不覆蓋。
- 使用者可選目標檔案／版本後確認覆蓋。
- 第一版不保留被覆蓋前版本；第一版不做多風格。

### 完成判準

- 引擎數字未被改寫；AI 只產敘事與 slots。
- 報告中每個數字可回溯到 `report_data.json` 或正式報表工具回傳。
- PPT 章節只來自本次使用者選擇且實際有資料的報表。
- `approvals.json` 只含合法 slot；無效 slot 有平台提示。
- manifest 含來源版本、hash、逐頁缺漏與追溯 metadata。
- 使用者可在匯出報告頁預覽真實 `.pptx` 並下載。
- 每批完成後回報「修改摘要／影響範圍／驗證方式／下一步」，等使用者確認後再進下一批。

## 開發備註

- 版本：v2.3，2026-07-30；上一版 skill 仍含 10 頁、市場線與浮水印流程，已被本版取代。
- 本 skill 目錄含 `SKILL.md`、`scripts/build_ppt.py`、`theme.json`，必須隨 repo／Docker image 一起提供。
- `theme.json` 是第一版單一風格來源；第二套 token 與模板檔延後。
- `python-pptx` 不吃 SVG，現有產生器用 `pymupdf` 把 SVG 轉點陣。真實預覽以 `.pptx` viewer 為準，不再用 CSS 模擬投影片判斷成品。
- 每次產製或修改需記工作紀錄，並遵守專案 TDD 規則。
