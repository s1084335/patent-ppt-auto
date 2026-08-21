# Design: 初階篩選（負面關鍵字）

## 現況證據（動工前實測，非推測）

### 1. 非原文欄位全為英文——這是本 change 需要 AI 轉換的唯一理由

全庫 281 件：

| 欄位 | 有值 | 含中日韓字 | 純 ASCII |
|---|---|---|---|
| `title` | 281 | **0** | 281 |
| `abstract` | 280 | **0** | 280 |
| `獨立項[KR,JP,US,CN,EP,IN]` | 252 | **0** | 251 |
| `文獻備註` | 281 | **281** | 0 |
| `title_original` | 202 | 200 | 0 |

實測同義詞對照（中文命中／英文命中）：

```
割草／mow          title 0/147   abstract 0/169   獨立項 0/163
刀片／blade        title 0/5     abstract 0/46    獨立項 0/59
傳動／transmission title 0/11    abstract 0/56    獨立項 0/52
把手／handle       title 0/6     abstract 0/74    獨立項 0/98
```

⚠ 使用者給中文詞直接比對，命中永遠是 0，**而且不會報錯**。這是典型的缺席型失效：
功能看起來在跑、只是「篩不到東西」，查不出原因會被歸咎於關鍵字寫錯。

### 2. 比對方式：子字串與完整詞界各自都會出錯，方向相反

同樣 281 件，三種比對方式實測：

| 詞 | 子字串 `ILIKE '%w%'` | 完整詞界 `~* '\mw\M'` | **前綴詞界 `~* '\mw'`** |
|---|---|---|---|
| `ion` | 265（invention、consumption） | 0 | **0** ✅ |
| `art` | 144（part、started） | 16 | **22**（收 article）✅ |
| `cut` | 102 | 13 | **97**（收 cutting、cutter）✅ |
| `mow` | 187 | 11 | **177**（收 mower、mowed）✅ |
| `lawn` | 125 | 105 | **125**（收 lawnmower）✅ |
| `blade` | 64 | 58 | **64**（收 blades）✅ |
| `battery` | 34 | 34 | **34** ✅ |
| `transmission` | 66 | 66 | **66** ✅ |

- 子字串會把 `ion` 掃成 265 件——**災難**
- 完整詞界會漏掉 `mower`／`blades`／`cutting`——**詞形變化正是要抓的**
- **前綴詞界在全部 10 個測試詞上都給出正確答案**

**決策**：採前綴詞界。此決策寫進 spec（PRE-003）並以測試鎖住，避免日後被改回
`ILIKE '%…%'`——那個改動不會讓任何測試變紅，除非有專門守它的測試。

### 3. 刪除的參照完整性：FK 全 CASCADE，真正的風險在沒有 FK 的地方

指向 `core_layer.patents` 的 **11 個 FK 全部是 `ON DELETE CASCADE`**：
`patent_attributes`、兩張 embeddings、`patent_figures`、`patent_people`、
`patent_sources`、`patent_search_terms`、`topic_assignments` 與三張 legacy 表。

→ **刪得掉，而且不會有任何東西擋住或警告。**

真正沒有參照完整性的是三處：

| 落點 | 型態 | 刪除後 |
|---|---|---|
| `app_layer.workspaces.patent_ids_json` | **JSON 陣列**（滑雪機 55／全庫 281／割草機 226） | 留下指向不存在專利的 id |
| `app_layer.report_artifacts`（**1020 列**） | `parameters` 內含 `patent_ids` 與 sha256 | 舊報表不可重現 |
| `derived_layer.workspace_excluded_patents` | `patent_id` **無 FK** | 留下孤兒列（本功能自己的表） |

**決策**：硬刪必須主動清理前兩處，並標記第三處受影響的報表版本。
單寫 `DELETE FROM patents` 是不完整的。

### 4. 既有剔除機制已完整，不做第二套

`backend/app/clustering/exclusions.py` 已具備：

- `store_ai_verdicts`：**AI 一律寫 `status='pending'`、`source='ai'`，寫不進 `excluded`**
- `pending_reviews`：待裁決清單（走部分索引，不全表掃）
- `excluded_patent_ids` / `analysis_member_patent_ids`：分析母體自動扣除
- `restore_patents`：還原並回填原主題指派
- `display_member_patent_ids`：顯示成員（目前回全部）

**決策**：初階篩選只新增「候選怎麼挑」，其餘全部複用。新增第二套排除表會製造
本專案已反覆踩過的「同一份知識兩個定義處」。

⚠ 既有 `CLU-007` 明訂**全庫 workspace 不得建立或確認排除**。初階篩選必須沿用此限制。

### 5. AI runner 的形狀已有底座

9 支既有 runner 中，`cli_gateway` 被 7 支使用、`ai_payload_file` 被 5 支使用。
各自只寫 `build_prompt` 與 `run_xxx`——那本來就該不同。

最接近的前例是 `ai_company_zh_name_runner`（432 行）：同樣是「外部名稱 → AI 轉譯 →
草稿 → 使用者逐筆確認才生效」。

⚠ **兩支 runner 留下同一個血淚註解**（`ai_candidate_explanation_runner:52`、
`ai_topic_backfill_runner:41`）：早期從別的 runner import `build_cli_command`，
而那是 `partial(tools=RESEARCH_TOOLS)`，於是**靜默取得 12 支工具＋MCP 取證權限**。

**決策**：新 runner 自行 `functools.partial(_gw_build_cli_command, tools=NO_TOOLS)`，
不從任何其他 runner import。

## 資料模型

### 新增：負面關鍵字表

概念欄位（實際命名於實作時定）：

| 欄位 | 說明 |
|---|---|
| workspace 外鍵 | 關鍵字的歸屬範圍 |
| 原始詞 | 使用者輸入，可為中文／英文／混雜 |
| 比對詞集合 | 英文詞陣列 |
| 確認狀態 | **未確認的比對詞不得用於比對**（PRE-002 的落點） |
| 啟用旗標 | 停用者不參與比對，但保留紀錄 |
| 時間戳 | 建立與最後更新 |

⚠ 「確認狀態」是護欄不是欄位裝飾：AI 寫入時一律為未確認，只有使用者操作能改。
與 `store_ai_verdicts` 只能寫 `pending` 同一個設計。

### 沿用：`workspace_excluded_patents`

**schema 完全不改**。初階篩選的命中寫入 `status='pending'`，`source` 標記為初階篩選線，
`reason` 記錄命中的關鍵字與比對詞，供 PRE-005「可追溯命中原因」。

## 流程

```
① 使用者輸入負面關鍵字（存 workspace）
        ↓
② ai:keyword_expand（NO_TOOLS）：中→英、同義詞、詞形
        ↓  產出＝未確認草稿
③ 使用者確認詞表（可增刪）      ← 決定權 1；未確認則流程停在此
        ↓
④ 前綴詞界比對 title／abstract／獨立項   ← 確定性，AI 不參與
        ↓  命中預覽（逐詞件數）
⑤ 套用 → store 至 pending
        ↓
⑥ 使用者裁決（逐筆／批次）       ← 決定權 2
        ↓
⑦ excluded＝封存：不進分群、不在瀏覽表、剔除名單可見可還原
        ↓  滿一年
⑧ 硬刪 ＋ 三處引用清理 ＋ 舊報表標記
```

⚠ ③ 是本 change 相對既有剔除線**新增的**確認點。理由：AI 轉出的詞直接決定篩出哪些
專利，不給使用者看就執行，AI 實質上在決定範圍——與「AI 只給建議」牴觸。
查詢式是短文本，確認成本低。

## 前端

初階篩選為**獨立頁、無左導覽項**，自瀏覽專利區入口進入。

此模式在本專案已有前例：案件比對有 `renderComparison()` 與 `renderMain()` 的
`case 'comparison'`，但左導覽沒有按鈕（2026-08-03 使用者「先移除掉」時只收入口）。

頁面四段：關鍵字 → 確認比對詞（含逐詞命中數）→ 待裁決 → 剔除名單。

⚠ 確認畫面**不顯示 SQL**（使用者裁決）。呈現形式為「原詞 → 英文比對詞 → 命中數」
的表格，每列可勾選與編輯。

瀏覽專利頁只加一個帶待辦數的入口；**不加就地裁決按鈕**（使用者裁決：一切作業在該頁）。
分類區既有的「標不相干」單筆入口不動。

## 風險與處置

| 風險 | 處置 |
|---|---|
| AI 轉出的詞過寬，篩掉不該篩的 | ③ 的命中預覽讓影響在套用前可見；且裁決權在使用者 |
| AI 轉出的詞過窄，篩不到 | 使用者可自行增補比對詞；轉換失敗也不阻斷 |
| 比對方式日後被改回子字串 | 以測試鎖住 `ion`／`mow`／`blade` 的期望命中數 |
| 硬刪不可逆 | 獨立切片、預設停用、dry-run、批次上限；於其餘切片驗收通過後才啟用 |
| 硬刪後舊報表不可重現 | 使用者裁決：照刪並標「來源已不完整」 |
| 新 job 的三處註冊漂移 | 補一支測試斷言三處集合相等（AIC-009） |
| 全庫 workspace 誤用 | 沿用 `CLU-007` 既有限制：全庫不得建立排除 |

## 未採用的替代方案

| 方案 | 為何不採 |
|---|---|
| 關鍵字命中直接標為待裁決、不經 AI | 中文詞命中恆為 0（見證據 1），功能形同不存在 |
| 關鍵字存全庫共用＋workspace 覆寫 | 兩層覆寫規則是第三次「同一份知識兩個定義處」的溫床；等真的出現共用需求再加 |
| 新建獨立的排除表 | 與 `workspace_excluded_patents` 重複，且復原、母體扣除都要再寫一份 |
| 在瀏覽表就地做裁決 | 使用者裁決：一切作業在獨立頁 |
| 半年封存、一年硬刪的兩階段 | 使用者釐清：封存即軟刪，一年內皆可反悔——少一個狀態少一批邊界情境 |
