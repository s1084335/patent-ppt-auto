# 專利分析報告 PPT 組版

把已產出的專利報表版本（數據＋圖表＋解讀）組成一份可直接對外簡報的 `.pptx`。
聚合數字全部來自報表引擎，組版由程式 deterministic 完成。

v5（2026-08-06）三份新核心文件：

- **`content_standard.md`**——內容專業度的唯一標準（由兩份定案範例反解），
  解讀與文案階段的 AI 寫每一段之前都要對照。⚠ 本 skill 服務**平台上所有
  workspace**，任何技術領域走同一份標準；範例是品質校準標杆，不是內容模板。
- **`data_access.md`** ＋ **`scripts/query_patents.py`**——解讀階段的 AI 可
  **自主查詢資料庫取證**（唯讀閘道），依它看到的圖表與數據自行判斷要撈什麼
  來把分析寫到標準要求的深度；不再受限於引擎預先聚合的欄位。

---

## 執行 Runbook

### 觸發時機

- 使用者在「匯出報告」工作台要求產生或更新專利分析報告 PPT。
- 使用者選定既有報表版本，要求預覽、產生或下載 PPT。
- 使用者要求換某一頁的版型後重產。

### 前置條件

1. 報表版本目錄已存在，且含 `report_data.json`、`artifact_manifest.json` 與圖表檔。
2. `narratives.json` 已產出（逐報表解讀）。若尚未產出或不完整，先跑 `ai:narrative`，
   完成後才接續本流程。解讀階段依 `report-narrative-flow.md` v5 執行
   （含自主取證：需環境變數 `DATABASE_URL` 供 `scripts/query_patents.py` 連線）。
3. 需要 `uv`，且能取用 `python-pptx` 與 `pymupdf` 套件（下方指令會自動取得）。

### 輸入契約

全部位於 `--report-dir` 指定的報表版本目錄內：

| 檔案 | 內容 | 角色 |
|---|---|---|
| `report_data.json` | `reports`／`family_reports` 兩個 bucket，每個報表含 `label_zh`、`rows`、`row_count`、`report_type`；`parameters` 含版本與選取的報表清單 | **數字唯一來源** |
| `artifact_manifest.json` | 每個 artifact 的 `file`、`report_name`／`report_names[]`、`artifact_type` | **圖檔對照唯一來源** |
| `narratives.json` | `reports.{key}.variants.{variant}` = `{headline, points[], text}` | 每頁標題與判讀要點的來源 |
| `approvals.json` | `{report_version, slots, layout_overrides}` | 使用者定稿文案與版型選擇 |

`approvals.json` 的 `slots` 只有一個合法鍵（v4：封面主標改由 `parameters.workspace_name`
確定性組成，`cover.title` 退場）：

```json
{
  "report_version": "<報表版本，須與報表目錄一致>",
  "slots": {
    "direction.body": "{\"situation\":[..],\"opportunity\":[..],\"direction\":[..],\"topics\":[..],\"conclusion\":\"..\"}"
  },
  "layout_overrides": { "5": "chart_with_points" }
}
```

`direction.body` 是結構化 JSON 字串（形狀見 `report_ppt_content_rules.md`）；
舊純文字仍可組版（條列過渡版面）但會記 `direction_unstructured` 警告。

其餘頁面的文字一律取自 `narratives.json`，不再另外請 AI 產一份；附錄頁沒有文案槽，
只渲染表格。`layout_overrides` 的鍵是頁碼字串，值是下方版型庫的 kind 名稱；
不認得的 kind 會被忽略，不會讓產檔失敗。

### 步驟

#### 1. 產生確認槽範本（首次或需要重填時）

```bash
uv run --no-project --with python-pptx --with pymupdf --python 3.12 python <skill 目錄>/scripts/build_ppt.py --init-approvals approvals.json
```

#### 2. 產生兩段 PPT 文案

由文案產製服務讀 `report_data.json` 與 `narratives.json`，只產 `direction.body`
一個 slot（結構化 JSON），寫進 `approvals.json` 的 `slots`。逐 slot 寫法與品質標準
以同目錄 `report_ppt_content_rules.md` 為唯一 runtime 來源，payload 必須載入該檔內容。

AI 回傳不在合法槽位清單內的 slot 名稱時自動過濾，並在任務進度提示；不直接讓工作失敗。

#### 3. 組版

```bash
uv run --no-project --with python-pptx --with pymupdf --python 3.12 python <skill 目錄>/scripts/build_ppt.py --report-dir <報表版本目錄> --approvals <approvals.json 路徑> --output-dir <輸出目錄>
```

完成時 stdout 逐行輸出：

```
pptx: <輸出的 .pptx 路徑>
manifest: <輸出的 .manifest.json 路徑>
sha256: <pptx 的 sha256>
pages: <頁數>
warnings: <警告則數>
```

之後逐則列出警告。呼叫端請解析前三行取得產物路徑與雜湊。

#### 4. 預覽閘門

工作台必須以真實 `.pptx` 渲染預覽，由使用者確認後才下載或重產；不得跳過預覽，
也不得改用 CSS 模擬投影片當正式預覽。若該版本已有 PPT，列出歷史檔案供選擇；
若沒有，顯示「產生 PPT」按鈕，按下時若 narrative 未產就自動先跑 narrative 再接續。

### 輸出契約

| 產物 | 說明 |
|---|---|
| `<report_version>.pptx` | 報告本體；同版本重跑不覆蓋，改產 `_r2`、`_r3` |
| `<report_version>.manifest.json` | 來源版本、來源目錄、`sha256`、逐頁資訊、`warnings`、`missing_slots`、`missing_reports` |

manifest 的 `pages[]` 每筆含 `page`、`kind`、`title`、`topic`、`report_keys`、`charts`、
`is_appendix`、`degraded_from`、`filled_slots`、`missing_slots`、`missing_reports`。

### 版型庫

🔴 **這是備選庫，不是必出清單**——每次出哪幾種由**內容**決定。

版型的來源有兩條路，兩條都只能從這份清單挑，都不得自創版型：

- **使用者指定**：勾選報表後以 `layout_overrides` 指定某頁用哪種。
- **依目標規劃**：CLI 依最大目標產出 SlidePlan，逐頁選 `layout_preset`。
  ⚠ 選了版型就要**給得出該版型需要的內容**（見下表「需要什麼」欄）；
  給不出就換一種版型或不開那一頁。

| kind | 用途 | 需要什麼 |
|---|---|---|
| `cover` | 封面：主標＋統計期間＋統計卡＋分析框架條 | — |
| `section_divider` | 章節隔頁（深色塊大字） | — |
| `chart_hero` | 大圖約 68% 寬＋右側完整要點面板＋底部核心結論條 | 圖＋要點 |
| `chart_with_points` | 單圖約 60% 寬＋右側要點框（＋必要時判讀限制框） | 圖＋要點 |
| `chart_wide` | 寬幅圖＋底部橫幅要點（扁長圖用） | 圖＋要點 |
| `comparison` | 同頁左右並排兩張圖，各配子標、編碼與要點 | 兩張圖＋各自要點 |
| `stat_callout` | 大數字焦點頁；也是圖檔缺失時的降級版型 | 一個關鍵數字＋要點 |
| `percentage_bars` | 佔比條列（如受理國分布） | 佔比資料 |
| `table` | 全寬表格（附錄） | 表格資料 |
| `table_with_points` | 表格＋右側要點 | 表格＋要點 |
| `exec_summary` | 結論先行頁：把可行動判斷放最前面 | **整頁敘述** |
| `walls_gaps` | 要迴避的牆 vs 可切入的空白，收斂成可行動清單 | **整頁敘述** |
| `reading_guide` | 判讀說明：母體口徑、可觀測性偏差、資料限制 | **整頁敘述** |
| `kp_quadrant` | Key Player 象限圖（國數×主題數，泡泡＝家族件數） | 象限圖＋要點 |
| `kp_deepdive` | 單一 Key Player 深入頁 | 該公司的具名證據 |
| `kp_cards` | 多個 Key Player 卡片並列 | 每張卡的具名證據 |
| `kp_compare` | 兩個 Key Player 左右對照 | 兩者的具名證據 |
| `direction` | 研發方向建議：左為綜合文案，右為報表依據欄 | `direction.body` 文案 |

⚠ 標「**整頁敘述**」的三種**沒有圖可以撐版面**：內容 100% 來自你寫的敘述，
留空就是一張白框。組版端會掃描正文區並標記 `empty_body`——但它只告警，
不會替你補內容。詳見 `content_standard.md`。

🔴 **`reading_guide` 頁有固定骨架，不要自由發揮**：副標一句話（本報告最該防的
誤讀）＋左區「可觀測性偏差」四條（角度固定：資料涵蓋邊界／同族放大／小樣本不等於
不重要／低密度要外部校正）＋右區三條編號結語。逐條寫法與範例原文見
`content_standard.md` 第 5-1 節，寫這頁之前先讀。

⚠ **口徑定義 ≠ 可觀測性偏差**：計數單位、同族合併、共同申請、分類覆蓋四段的
正式說法在 `report_data.json` 的 `table_display.reader_guide`（每則含 `title`／
`body`）——**要用就逐字照抄**（憑印象改一個字就會和頁尾母體註記自相矛盾，
且沒有檢查會擋），但它們回答的是「數字怎麼算」，不能拿去頂替上述四條
「不能怎麼推論」。落點通常是頁尾註記或附錄，不是本頁正文。

成對報表在任何情況下都不會被合成同一張圖。預設呈現：IPC／CPC 的 L4 與 L5
**同頁左右並排**；機會矩陣技術面／功效面與主題分布技術／功效**預設分頁**
（象限圖與主題表資訊密度高，並排太小）；年度矩陣只上前 10 名主表圖，
`_more` 長尾圖不上 PPT。使用者可經 `layout_overrides` 換版型。

### 出頁規則

- **依目標規劃**時頁序由 SlidePlan 決定，本節其餘規則不適用；改由規劃端
  自負「這頁撐不撐得起來」的判斷（見上方版型庫）。
- 以下為**固定頁序**模式（使用者勾選報表）的規則：
- `cover` 與 `direction` 恆出。
- 其他頁面必須至少有一個 `report_key` 在 `report_data` 真的有資料才出頁；
  未選或無資料的報表不出頁，頁數由使用者勾選的報表自然決定，不設上限。
- 基礎大綱沒列到、但本次有資料的報表，自動插在第一個附錄頁之前。
- 圖檔一律以 `artifact_manifest.json` 反查（`report_name` → 檔名），不依報表代號猜檔名。
- 找不到可用圖檔的頁面降級為 `stat_callout`，改用該報表的關鍵數字呈現；
  PPT 內不會出現「圖檔待產出」這類佔位文字，每頁都有視覺元素。

### 缺漏處理

缺文案、缺報表、缺圖、解讀格式較舊，一律**不印在 PPT 上**，只寫進 manifest 的
`warnings`／`missing_slots`／`missing_reports`，由平台任務進度呈現。分群版本不一致
只提醒，不阻擋輸出。

manifest `warnings[]` 的 `type` 值：

| type | 意義 | 處理 |
|---|---|---|
| `narrative_missing` | 該頁找不到對應解讀，要點改用引擎數字 | 補跑該報表的解讀 |
| `narrative_fallback` | 解讀只有長文、缺 `headline`／`points`，已切段落並依版面截斷 | 升級解讀產出格式 |
| `headline_derived` | 解讀未給標題，標題取自要點首句 | 同上 |
| `chart_missing_degraded` | 找不到圖檔，該頁已降級 | 確認圖表是否產出 |
| `artifact_manifest_missing` | 整份圖檔對照表不存在 | 重新產製報表版本 |
| `out_of_bounds`／`margin_violation`／`text_overlap`／`text_overflow_estimated` | 版面自檢發現超界、邊距不足、文字重疊或文字裝不下 | 視為組版缺陷，需回報 |

前五種屬資料面提醒，後四種屬版面缺陷；正常情況下後四種應為零。

### 完成判準

- 引擎數字未被改寫；AI 只產 `cover.title` 與 `direction.body`。
- PPT 章節只來自本次實際有資料的報表。
- manifest 含來源版本、`sha256`、逐頁缺漏與追溯 metadata。
- 版面自檢沒有超界、邊距不足或文字重疊。
- 使用者可在匯出報告頁預覽真實 `.pptx` 並下載。

---

## 開發備註

### 版本

v3（2026-07-30 重建）。v2.3 功能可跑但實機驗收不合格：每頁 400–500 字單段字牆、
圖檔對不上而顯示佔位、圖片溢出版面 3.5 吋、封面標題壓字、主題色 accent 完全沒用到。
v3 針對這五項逐一重做，並補上產後自檢把版面缺陷變成可觀測的 warning。

### 檔案分工

| 檔案 | 責任 |
|---|---|
| `SKILL.md` | 流程、產物、出頁規則與驗收（本檔） |
| `scripts/build_ppt.py` | deterministic 組版與產後自檢，不呼叫 AI |
| `theme.json` | 配色、字級、**全部版面座標**、裝飾參數的唯一來源 |
| `report_ppt_content_rules.md` | 文案產製服務的 runtime 規則 |
| `report-narrative-flow.md` | 上游逐報表解讀（`ai:narrative`）的口徑（v5 含自主取證階段） |
| `content_standard.md` | 內容專業度唯一標準（兩份定案範例反解；全 workspace 通用） |
| `data_access.md` | 自主取證的資料庫地圖與守則 |
| `scripts/query_patents.py` | 唯讀查詢閘道（連線層強制 read-only＋30s 逾時＋列數上限） |

七份檔案必須隨 repo／Docker image 一起提供；缺 `build_ppt.py` 或 `theme.json` 時
版型 API 會回 503（部署環境沒帶到 skill 檔案的明確徵狀）。

### 設計決策

- **座標唯一來源**：renderer 內不得出現座標數字字面值，由 AST 契約測試
  `tests/test_ppt_geometry_single_source.py` 把關。前端投影片縮圖預覽讀同一份
  `theme.json` geometry，座標分岔是本專案反覆出現的問題，故硬性隔離。
- **圖檔反查**：實際檔名與報表代號不同名（受理國分布的圖叫
  `jurisdiction_distribution.svg`、趨勢圖叫 `annual_trend.svg`），且同一報表可能對應
  多張圖（IPC 的 L4/L5、機會矩陣的技術面/功效面）——多圖正是成對呈現的來源。
- **拆頁時收窄 report_keys**：多圖頁拆成單圖頁時，`report_keys` 一併收窄到該圖真正
  對應的報表，否則兩頁會抓到同一段解讀、印出一模一樣的標題與註腳。
- **要點按條數分配行數**：依序填到滿會讓第一條吃光版面、後面的「意涵」「後續」整條
  消失，等於只給讀者半個判讀。改成每條至少一行、各自截斷。
- **圖片先插入再依框縮放**：只給 `width=` 讓高度自由伸展，遇到高瘦圖就會往下溢出。
- **字體要寫三處**：只設 `run.font.name` 只會寫 `a:latin`，中文會被 PowerPoint 退回
  新細明體；`_set_font` 同時寫 `a:latin`／`a:ea`／`a:cs`。
- **表格內距壓到 0.02 in**：python-pptx 預設 0.05 in，而 PowerPoint 列高只增不減，
  預設內距會讓整張表往下撐爆版面；同時依欄寬截字避免自動換行撐高列。
- **文字容量估算加 epsilon**：`1.5 / (40/72*1.35)` 在浮點下是 1.9999999998，直接 `int()`
  會少算一行，讓剛好兩行的標題被誤判成裝不下而截字。

### 已知限制（皆為上游契約，非本 skill 缺陷）

- 上游解讀契約已升級三件套（`headline`／`points`／`text`，report_narrative_v4）；
  舊格式資料仍可組版（切段落 fallback＋警告），重跑解讀後警告自然消失。
- `cluster_topic_table` 與 `opportunity_quadrant` 已由引擎寫進 `report_data.reports`
  （c62e680）；舊報表版本仍缺這兩鍵，重產報表即補齊。
- `position_overrides`（拖曳座標）已於 v3 移除：座標唯一來源是 `theme.json`，
  再開一條「每頁各自存一份座標」的路等於把座標分岔重新引回來。上游仍可送這個 key，
  組版程式直接忽略，不會壞。

### 驗收方式

1. 契約測試：
   - `tests/test_ppt_layout_contract.py`（版型庫、成對呈現、圖檔反查、缺圖降級、
     解讀容錯、產後自檢、字體字級、accent 使用、封面統計卡、manifest 完整性）
   - `tests/test_ppt_builder.py`（選擇驅動出頁、不浮水印、不覆蓋重跑、SVG 轉檔快取）
   - `tests/test_ppt_builder_dynamic_pages.py`（動態插頁錨點、版型覆寫）
   - `tests/test_ppt_geometry_single_source.py`（座標唯一來源 AST 檢查）
2. 實機轉圖目視：對真實報表版本目錄跑組版，再用 `ppt-tools` 的 `pptx_to_png.py`
   （走本機 PowerPoint COM，輸出即使用者開檔會看到的樣子）逐頁檢查無溢出、無壓字、
   無佔位文字、accent 有實際使用、每頁有視覺元素、標題為判讀式。
3. 每次產製或修改需記工作紀錄，並遵守專案 TDD 規則（Red 先真跑並記錄失敗原因 →
   最小 Green → 必要才 Refactor）。
