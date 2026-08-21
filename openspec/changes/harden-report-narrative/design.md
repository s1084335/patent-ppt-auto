# Design: 報表解讀的可信度與人工介入

## 穩定性邊界（先讀這段）

使用者 2026-08-21 明示：**HTML 匯出檔的版面、內容與章節都可能再修**，現階段先照現況做。

⇒ 本 change 的規格**不得綁定任何版面細節**：不指定字級、不指定章節順序、不指定小標名稱、
不指定解讀出現在頁面哪個位置。規格只規範「**哪份文字要受檢**」「**檢查什麼性質**」
「**人能不能改**」。

⚠ 這不只是為了配合改版，也是 EXP-013 的直接推論：判準一旦綁上版面元素，就變成
「格式判準」，正是第一世代的病灶。**版面可變，判準不變**——這兩件事互為因果，不是巧合。

## 一、來源：破口在哪

`#452` 的四層護欄逐層失效，共通點是**全部只驗字面，沒有一層驗行為**：

| 層 | 應該擋住 | 實際 | 為何沒擋住 |
|---|---|---|---|
| 提示詞 | 要求 CLI 取證 | 未取證 | 提示詞是請求，不是約束 |
| 工具權限 | 給了取證工具 | 一次沒用 | **給了工具不等於用了工具** |
| 輸出契約 | `evidence` 應存在 | 欄位不存在 | 契約沒宣告 `evidence`，與流程文件矛盾 |
| 守門測試 | 應變紅 | 全綠 | 測試比對**提示詞裡有沒有那串字**，而字一直都在 |

第四層最值得記：`test_narrative_requires_research.py` 是為了防這件事而寫的，
它**如實通過了它斷言的東西**——只是斷言的是字串，不是行為。

⇒ 本 change 的修法一律落在**行為與資料**上：欄位在不在、查詢數是多少、檢查跑了沒有。

## 二、內容：鎖鎖錯了對象

`validate_narrative_contract` 的鎖一~鎖九全部作用在 `points`。實測使用者匯出的
`割草機.html`：**17 個變體全是長文、`points` 0 條**。

```
AI 產出 ──→ points  ←── 鎖一~鎖九全在這裡
        └─→ text    ←── HTML 顯示這個，零檢查
```

長文只在鎖三（數字一致）被當作**參照物**——拿來檢查 points 的數字對不對，
它自己不受檢。

⇒ EXP-011 只改**檢查對象**，判準沿用既有九把鎖。不新增判準是刻意的：
新判準要有依據，而依據要從實跑分布來，現在沒有。

## 三、自檢的形式：為什麼是 runner 端強制

### 不能照 deck 抄

deck 的修稿輪寫在 CLI 自己的 skill 裡（`SKILL.md:396`），因為 deck 的 CLI **有 Bash**，
自己就能跑 `check_content.py`。解讀的 CLI 只有 `RESEARCH_TOOLS`，沒有 Bash。

### 三種形式的取捨

| 形式 | 做法 | 判斷 |
|---|---|---|
| **A · runner 端迴圈** | runner 跑檢查 → 違規項進下一輪 prompt → 重叫 CLI 修 | ✅ 採用 |
| B · 唯讀 MCP 檢查工具 | CLI 自己呼叫拿違規清單、自己修 | ❌ 見下 |
| C · 給 CLI Bash | CLI 自己跑檢查腳本 | ❌ 擴權 |

🔴 **B 必須否決，理由就是本案的事故本身**：`#452` 的 CLI 03:29 讀過 `data_access.md`、
知道有取證工具，**然後選擇不呼叫**。一個「CLI 可以選擇不叫」的自檢，與沒有自檢等價。

⇒ **檢查必須由 runner 強制執行，不能是 CLI 的選項。**
⚠ 實作時很可能被「開支工具給 CLI 自己檢查比較省」帶偏——那會退回同一個坑。

### A 的續談限制（待實測）

`cli_gateway` 目前只支援 `-p`、`--allowedTools` 與 model flag，**沒有 `--resume`**。
無續談時「餵回」＝重開一次 CLI，把前一版產出與違規項一併放進 prompt。

- 可行，但每輪要重讀 `report_data.json`（`#452` 的 CLI 段約 17 分鐘）
- **規劃決定**：先照「重開、帶完整上下文」寫規格（保守、確定可行）；
  輪數上限因此壓低。resume 列為實作期最佳化，需先實測旗標可用性

## 四、不重蹈第一世代的三條硬規

來源：`.agents/context/report-professionalism-spec.md` 的 16 個問題。與解讀直接相關的三條：

### 規則 1 · 判準檢查性質，不檢查格式（→ EXP-013）

> 問題 14（模板化）：🔴 **這是我們自己造成的**——「建議進一步檢視 X，以確認 Y」是
> `ai_narrative_runner.py:515` 與 `report-narrative-flow.md:221` **硬性規定的固定句型**。
> 病因**不是段數，是「固定欄位名 ＋ 固定句型」**。

⚠ 千篇一律的原因不在模型，在判準。加閘門時最容易犯的錯就是「規定一個好格式」——
那會直接複製第一世代。

### 規則 2 · 固定文案不給 AI 重寫（→ EXP-014）

> 問題 16：⚠ **固定文案不得交給 AI 每次重寫**……**一致性比新鮮度重要**。

限制與涵蓋範圍說明的價值在於每次都一樣。措辭浮動會被使用者讀成「口徑改變了」。

### 規則 3 · 規則要落在資料流上（→ 貫穿全案）

> 問題 13：**比照 narrative 的 contract 鎖，光寫在 prompt 沒有程式驗證等於沒有規則**。

⚠ 諷刺的是，`#452` 的破口正是這條的反例：narrative contract 鎖**確實存在**，
但它鎖的是沒人顯示的 `points`。**規則落在資料流上還不夠，要落在「交付物實際用的那條」上。**

## 四之二、內部指示不得洩漏（EXP-025）

使用者 2026-08-21：「解讀限制給 CLI 看的不要洩漏」。

派工提示詞裡的字數上限、格式要求、契約欄位名、工具規則都是**作業指示**，
不是給讀者看的內容。它們出現在解讀裡有兩重代價：佔掉本該講判讀的篇幅，
以及讓讀者看到系統的內部形狀。

⚠ **洩漏的常見形式不是整段複製，而是用自己的話覆述**：

| 洩漏形式 | 為何難擋 |
|---|---|
| 「依規定本段不超過 N 字」 | 字面與提示詞不同，關鍵字比對抓不到 |
| 「本次未取得足夠資料故不作推論」 | 看起來像誠實揭露，實際是覆述禁止事項 |
| 「已依契約提供 evidence」 | 揭露了內部欄位名 |

⇒ 檢查對象是**內容性質**（這句話說的是資料，還是說的是作業規則），不是字串比對。
🔴 用關鍵字黑名單實作會同時失效**且**違反 EXP-013（判準不得依格式）。

⚠ 與 EXP-014 的分界：程式產生的限制說明是**給讀者的**（資料涵蓋範圍），要留；
覆述給 CLI 的作業指示是**內部的**，不能出現。兩者都叫「限制」，但一個是資料事實、
一個是作業規則。

## 五、人工介入：現況與落點

### 顯示端已經寫好了

```js
// index.html:4927
// 解讀區：AI 原稿（ai_original）與人工修改（manual_text）分欄，顯示以人工稿優先。
const shown = (manual_text != null) ? manual_text : ai_original;
const edited = (manual_text != null && manual_text !== ai_original);
```

缺的只有三件：編輯入口、保存端點、資料落點。

### `view.edits` 是死路徑

`manual_text` 讀 `view.edits.narratives`，而 `edits` 是**匯出工作台的 localStorage 結構**，
已隨工作台移除（`EXPORT_EDIT_KEY_PREFIX` 一併清掉）——現在恆為 `undefined`。

⇒ 要改成從 `narratives.json` 讀。顯示邏輯本身一行都不用動。

### 落點：`narratives.json` 分欄（使用者裁決）

| 落點 | 取捨 |
|---|---|
| **`narratives.json` 分欄** ✅ | 顯示端零改動；隨版本走；匯出檔自然帶到人工稿 |
| 獨立表 | 契約不動，但匯出要 join，且多一個定義處 |
| localStorage | 換瀏覽器就沒了，與「儲存」的期待不符 |

### 逐報表獨立（使用者裁決）

「各自解讀獨立修改」不是新概念——`index.html:4035` 的註解 2026-07-29 就寫著
「**重解讀是個報表獨立**」，`report_keys` 三段（API → handler → runner）**已全通**。

```
每張報表 × 每個變體 = 一個獨立單位
  ├─ ai_original   ← 逐報表重跑（report_keys，已存在）
  └─ manual_text   ← 逐報表編輯保存（本 change 新增）
     顯示：人工稿優先（已存在）
```

⇒ 重跑 A 不碰 B 的人工稿；重跑 A 自己是使用者對 A 主動按的，AI 就重寫 A 的原稿。
**不需要衝突裁決 UI**。人工稿仍保留在 `manual_text`，使用者自行決定要不要沿用。

### ⚠ 不得誤觸的既有守門

`test_export_edit_mode_removed` 斷言 `export-edit-toggle` 與 `toggleExportEditMode`
兩個字串不得出現。它守的是**已退場的匯出工作台**，原文：

> 編輯用 render 分支暫留為不可達路徑，但**入口不得存在**——入口在就會被按。

新入口在報表種類頁的內嵌解讀區，與該守門不衝突，但**不得沿用那兩個名字**。

## 程式落點

| 位置 | 改什麼 |
|---|---|
| `ai_narrative_runner.build_prompt` | 形狀宣告補 `evidence` |
| `ai_narrative_runner.validate_narrative_contract` | 檢查對象擴及長文；新增 evidence 檢查 |
| `ai_narrative_runner`（新） | 自檢迴圈：檢查 → 違規進下一輪 prompt → 重叫 → 輪數上限 |
| `handlers.handle_ai_narrative` | 結果白名單補三欄 |
| `report-narrative-flow.md` | `evidence` 由選填改必要 |
| `index.html` AI 任務卡 | 顯示 `contract_warnings` |
| `index.html` 解讀區 | 編輯入口＋保存；`manual_text` 改讀 `narratives.json` |
| API（新） | 人工稿保存端點 |

⚠ 稽核落點維持既有 `mcp_query_audit` 的唯一來源，`ai_narrative_runner` **只讀不定義**
——否則稽核落點就有了第二個定義處。

## 輸出契約

`narratives.json` 頂層新增（形狀正文以 `report-narrative-flow.md` 為唯一來源，此處僅示意）：

```json
"evidence": {
  "<report_key>": [
    {"claim": "...", "queried": "...", "patent_ids": [95, 102, 117]}
  ]
}
```

變體層新增人工稿欄（與 AI 原稿分欄，不覆蓋）：

```json
{"text": "AI 原稿…", "manual_text": "人工修改後…"}
```

`workflow_outputs.data_json`（`output_type = job_result:ai:narrative`）新增三欄：

```json
{
  "query_audit": [{"tool": "query_database", "rows": 42, "truncated": false, "status": "ok"}],
  "query_count": 1,
  "contract_warnings": ["本次解讀未取證（query_count=0）"]
}
```

⚠ 三欄一律存在。零值時為 `[]`／`0`／`[]`，**不得因為值為空而省略**——欄位缺席與
「查過但沒查到」在消費端無法區分，正是本次事故難以察覺的原因之一。

## 測試對照

| Requirement / Scenario | 目標測試 |
|---|---|
| EXP-008 有取證 / 未取證 / evidence 空 | `test_narrative_requires_research.py`（改為行為驗證） |
| EXP-008 組版端不受影響 | `test_chart_sections.py`（既有；確認缺 `evidence` 仍正常組版） |
| EXP-009 有查詢 / 零查詢不得省略欄位 | `test_narrative_requires_research.py`（實跑 handler，斷言結果三欄存在） |
| EXP-009 稽核讀取失敗不得拖垮工作 | `test_mcp_query_audit.py`（既有，補讀取失敗案例） |
| EXP-010 有警告 / 無警告 | `test_api_ai_tasks.py` ＋ `test_api_frontend.py`／`test_frontend_js_syntax.py` |
| EXP-011 長文受檢（三個 scenario） | `test_narrative_contract.py`（新增長文案例） |
| EXP-012 修稿輪（四個 scenario） | `test_narrative_selfcheck.py`（新增，含輪數上限與不擴權斷言） |
| EXP-013 判準不依格式（三個 scenario） | `test_narrative_contract.py`（同內容異格式須同結果） |
| EXP-014 固定文案穩定 | `test_population_notes.py`（既有，補重複產生一致性） |
| EXP-025 內部指示不洩漏（三個 scenario） | `test_narrative_contract.py`（新增）＋ 交付前逐則人工檢視 |
| EXP-015 編輯保存（四個 scenario） | 新 API 測試 ＋ `test_api_frontend.py` |
| EXP-016 逐報表獨立（三個 scenario） | `test_narrative_report_keys.py`（新增） |

⚠ **行為守門的真 Red 驗證**：新測試必須在**人為移除 handler 白名單任一欄**時變紅。
這一步不可省——破口 4 的教訓正是「有測試」不等於「守得住」，寫完要實際證明它會紅。

## 風險與回復方式

| 風險 | 評估 | 處置 |
|---|---|---|
| 🔴 加閘門時順手規定「好格式」，複製第一世代模板化 | **高**。這是最自然的加法 | EXP-013 明訂判準不得依格式；驗收判準 8 逐條檢視 |
| 🔴 實作時把自檢改成 CLI 自呼叫的工具（比較省） | **高**。省事且看似合理 | design 三、EXP-012「不擴權」scenario；驗收要驗工具集未變 |
| 修稿輪讓解讀耗時倍增 | 中。無 resume 時每輪重讀全文 | 輪數上限壓低；逾時仍由 `cli_timeout_seconds` 控制；實跑後檢視是否加 resume |
| 模型為湊 `evidence` 做無意義查詢 | 中。本案只驗有無、不驗品質 | 警告文字寫「未取證」而非「查詢次數不足」，避免誘導湊數 |
| 人工稿被重跑覆蓋 | 中 | 分欄保存，AI 只寫 `text`；EXP-016 逐報表獨立 |
| 版面改版推翻本案規格 | 低（已處置） | 穩定性邊界：規格不綁定任何版面細節 |
| 新編輯入口誤觸既有守門 | 低 | 不沿用 `export-edit-toggle`／`toggleExportEditMode` 命名 |
| 既有 `narratives.json` 全缺 `evidence` | 低。警告只在解讀當次產生 | 不回填；EXP-008 已明訂組版端忽略 |

**回復方式**：三支柱彼此獨立，可逐支柱 revert。無 migration、無 schema 變更，
revert 後 `workflow_outputs` 的多餘欄位由消費端忽略，`narratives.json` 的 `manual_text`
亦為 additive。前端為靜態檔，revert 後重新載入即生效。

## 未採用的替代方案

| 方案 | 為何不採 |
|---|---|
| 零取證直接讓 job 失敗 | 會讓使用者連「寫壞的解讀」都看不到，無從判斷問題在哪。先觀察分布 |
| 開唯讀 MCP 檢查工具給 CLI 自檢 | CLI 可以選擇不叫——與 `#452` 同一個坑 |
| 給 CLI Bash 讓它自己跑檢查 | 擴權，且與取證工具「給了不等於用了」同一問題 |
| 人工稿存獨立表 | 匯出要 join，且人工稿與 AI 稿分處兩地，多一個定義處 |
| 人工稿存 localStorage | 換瀏覽器就沒了 |
| 重跑時做衝突裁決 UI | 逐報表獨立後不需要——重跑哪張是使用者對那張主動按的 |
| 順便新增品質判準 | 新判準要有依據，依據要從實跑分布來，現在沒有 |
| 順便改版面／章節 | 使用者明示版面還會再修；另案 `restructure-report-sections` |
