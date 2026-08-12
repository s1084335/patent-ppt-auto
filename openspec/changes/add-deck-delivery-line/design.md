# Design: 簡報產製接進系統（add-deck-delivery-line）

## Context

skill 九步（2026-08-12 實掃）：①intake ②plan_deck ③chip 重排 ④fit 轉 PNG
⑤撰稿 ⑥check_content ⑦make_deck ⑧audit_deck ⑨COM 轉圖目視。
①–④、⑥–⑧是純機械腳本（uv 一行跑）；⑤是唯一 AI 判斷步；⑨依賴 Windows Office。
既有 AI 通道：Companion 領 job → runner 組 prompt → headless CLI → 驗證回存
（`ai_narrative_runner` 模式）。

## Decisions

### 1. runner 驅動機械步、CLI 只接撰稿（接口的核心形）

```
ai_report_deck_runner（Companion 內，host-agnostic）
  1. materialize_version（沿 narrative 既有函式）→ 版本目錄
  2. subprocess：assemble_from_version → plan_deck →（標記則 chip 重排）→ fit
  3. build_prompt：plan.json＋report.json 素材路徑＋撰稿契約
     → headless CLI → 產 work/content.json（唯一輸出檔，同 narrative 權限面）
  4. subprocess：check_content → 組版（B 案：SVG→量測→轉換）→ audit
     → **逐頁截圖**
  5. 🔁 CLI 目視迴圈（見下）：CLI 看逐頁 PNG → 有問題修 content.json
     → runner 重跑 4 → 再看；通過或達上限為止
  6. 回存：pptx＋逐頁 PNG 寫 <DECK_ARTIFACT_ROOT>/<version>/，manifest＋紀錄寫 DB
```

**目視迴圈＝現行開發流的「看了回去調」原樣搬進產線，只換工具**
（2026-08-12 使用者明示保留）：開發流是我目視 COM 轉圖後回頭調整再轉再看；
產線由撰稿 CLI 對 Chromium 逐頁截圖做同一件事——檢查清單沿用 skill 現行目視
清單（重疊、裁切、圖內字可讀、版面平衡、行首標點），發現問題**只能改
content.json**（縮寫、改寫、拆頁、轉純文字頁——與今天人工調整的合法動作
一致；圖表、版面引擎、字級規格照授權界線不得動），runner 重組版重截圖後
再看。**迴圈上限＝產線參數，不沿 skill 開發紀律**（2026-08-12 使用者定案
「產品的不要沿用，改成產線參數」）：skill 的「同一問題最多修兩輪」是
開發時人機協作的停損（修不動就停下來找人談），出處為專案 AGENTS.md
「Token 節制」試行條款；產線語意不同——上限到了＝job failed＋使用者按重產，
故改為可調參數 `DECK_VISUAL_LOOP_MAX_ROUNDS`（env／設定，**預設 4**，
調整不改程式）。達上限即 job failed 並附最後一輪的目視發現。
check_content 的閘門紅走同一個迴圈、吃同一個參數，不另設第二條重試路。

**預設 4 的依據**（2026-08-12 使用者定案，回顧開發實績後定）：開發側交付
`_v17` 走了十幾輪，但其中大半是**版面引擎調整**（字級、斷行估算、撞版）與
**規格層來回**——前者 B 案已從根消滅（斷行寫死＋Chromium 實測 BBox）、
後者已定案凍結，兩類都不落在產線迴圈裡。產線迴圈只扛**純內容輪**
（縮寫、改寫、拆頁），該類實績約 2–4 輪：首輪抓大問題、次輪掃殘留、
三輪多為確認。取 4 留一輪餘裕救「差一口氣」，又不致把「撰稿方向錯了」
磨成半成品。每輪成本＝機械重組版（秒級）＋看一批 PNG，遠低於 fail 後
使用者整份重產。⚠ 實跑後依累積數據調參，不改程式；**每輪都要落進
目視迴圈紀錄**，才有數據可回頭校準這個值。

為什麼不讓 CLI 一路跑到底：那需要給 CLI 跑任意 uv 腳本的 Bash 白名單
——權限面等於開發機 agent，與「CLI 只輔助、不碰執行面」的架構原則相反；
機械步本來就是確定性程式，讓 runner 跑還能拿到結構化 exit code 當閘門。
skill 的「自主執行」章節在產品側由 runner 具體化，開發側手動流程仍照 SKILL.md。

### 2. 撰稿契約＝skill 第 5 步的機器可驗形

CLI 輸入：`plan.json`（頁序、structure_checklist、骨架頁）＋`report.json`
（texts／tables／notes）＋narrative.md 寫法規範（隨 skill 遷入產品 repo，
runner 以路徑注入 prompt，與 narrative 線的 prompts/ 同模式）。
CLI 輸出：`content.json` 一檔。驗證：check_content 既有閘門（佔位符未清、
字數、裸數字、措辭）就是撰稿的 contract validator——不另寫第二套。

**取證通道保留（2026-08-12 使用者明示「不能擋掉」）**：撰稿 CLI 配備
**唯讀 MCP 資料庫取證工具**——與 `ai:narrative` 線完全同級（沿 2026-08-09
「取證通道統一走 MCP」與 2026-08-10「代表專利走 CLI 自行查」定案）。
競爭者構型頁顆粒度不足時，CLI 依 plan.json 的 `claim_lookup`（patent_ids）
自行查請求項原文；skill 的四條硬規則（只讀不寫、只補敘述不補統計、
新名詞標來源專利號、斷言範圍＝證據範圍）進撰稿 prompt 原樣約束。
權限面＝「讀素材＋唯讀 MCP 取證＋寫一檔」＝narrative 現況，仍不需 Bash 白名單。
（`fetch_claims.py` 留在 skill 供開發側手動流程用；產品側取證走 MCP，不跑腳本。）

### 3. 產物落點（2026-08-12 定案的實作形）

- `DECK_ARTIFACT_ROOT` 環境變數（沿 `MODEL_ARTIFACT_ROOT` resolver 前例）；
  正式＝NAS 掛載點，現階段＝本機目錄。DB 只存相對 key。
- DB：`workflow_runs`（job 本體）＋`workflow_outputs`（manifest：
  based_on_version、pptx 相對 key、SHA-256、大小、閘門摘要、
  是否經逐頁目視）。**不進 report_artifacts**（那是版本內容資料；
  deck 是衍生交付物，混放會讓版本目錄語意變髒）。
- **過程紀錄一併回存（2026-08-12 使用者指出規格缺口）**：narrative 線的
  取證 audit（8 欄 JSONL）已隨 job 落 DB，deck 線比照——
  ①**撰稿取證 audit**（同一條 MCP 通道自帶，reset-per-task）
  ②**目視迴圈紀錄**（每輪：發現了什麼、改了 content.json 哪裡）——
  失敗路徑本就要求「附最後一輪目視發現」，成功路徑同樣留全程，
  「這份 deck 的每段話查了什麼、改過幾輪」才可回放。
- 失敗不落半成品：pptx 先寫 work dir，全閘門過才搬進 ROOT。

### 4. 組版輸出層＝B 案（2026-08-12 使用者定案；借鑒 ppt-master 確定性中間層）

精準度流失的唯一來源是「PowerPoint 保留排版決定權」（原生文字框開檔時自行
換行——估算器猜、COM 驗、08-02 實證連 COM 都與實機有微差）。B 案把決定權
全收回引擎：

```
deck_layout 幾何引擎（原樣）→ 每頁組 SVG（文字逐行斷好、絕對定位）
  → Chromium 量測實際 BBox（取代估算）＋逐頁截圖（＝產線目視，Linux 原生）
  → 窄 SVG→DrawingML 轉換器 → 原生 PPTX（逐行文字定位寫死、關 wrap）
```

- **窄**是關鍵：只支援本 skill 的元素詞彙（矩形卡、逐行文字、圖片、線），
  五種頁型（封面／圖表頁／文字頁／標籤頁／路線圖），不支援 SVG 標準全集。
- PPTX 內文字仍是原生可編輯，但行斷點固定（改長會溢出）——ppt-master 同款取捨。
- **開發期一次性映射校驗（Windows 開發機）**：五頁型逐一
  「Chromium 截圖 vs COM 轉圖 vs 實機開檔」三方對照，映射成立後固定；
  COM 自此只是開發量尺，**不進產線**。產線（Linux）零 Windows 依賴。
- **⚠ 字型是部署前置（2026-08-12 使用者提問後定）**：Playwright／Chromium
  量測與截圖在 Linux 原生可用，但 `getBBox` 結果取決於**裝了什麼字型**
  ——現行 SVG 宣告 `Segoe UI`（Windows 字），Linux 伺服器無此字會 fallback，
  字寬、撞版判定與截圖外觀全變。deck 組版 SVG 必須宣告**定案字型**並在
  伺服器安裝同一份（建議開源 Noto Sans TC；用微軟字型需確認授權）；
  映射校驗以該字型為準。
- 目視從「開發側手動」升級為**產線內建**：每次產製都出逐頁 PNG（截 SVG＝
  截成品），兼作前端 deck 紀錄的逐頁預覽。
- `regression.py` 像素基準改比 SVG 截圖；版面回歸在改 skill 時守，
  每次產製的變因只有內容，內容由 ⑥ 守。
- 曾評估之替代案備查：A＝內網 Windows 轉圖代理（零開發、保真＝現況含已知
  微差、長期養機器）；LibreOffice（引擎不同，斷行不可信，早經否決）；
  Aspose（商用授權費）。B 以中等開發量換「精準度提升＋零 Windows 依賴」。

### 4b. 封面素材：技術名稱＝workspace 名稱（2026-08-12 使用者指定）

runner 依 `version_meta.json` 的 workspace 歸屬查回 **workspace 名稱**
（如「自走式割草機」），注入 intake 產出的 `report_meta` 作封面技術名稱
（deck_title／eyebrow 的來源）；全庫版本退回既有報表標題。
撰稿 CLI 只消費不另查——素材備妥是 runner 的責任。

### 5. skill 遷移與單一落點

`skills/html-report-to-deck/`（產品 repo）；SKILL.md 重寫為兩區：
Runbook（觸發、九步、契約、閘門——供零背景 CLI／開發者照跑，路徑全走
`<S>`／env）＋開發備註（設計原因、COM 驗收、regression、pitfalls 引用）。
`regression_baseline/`、`references/` 隨遷。中央 `.agents/skills/` 份刪除，
`.agents/context/README.md` 路由更新。⚠ Dockerfile **不需要** COPY skills
——deck runner 跑在 Companion 側（伺服器化後與 repo 同機），backend 容器
不讀 skill 檔（舊 build_ppt 的 503 教訓不適用）。

### 6. 前端最小面

版本區加「產製簡報」按鈕（POST /ai-tasks，task_type=ai:report_deck，
payload=based_on_version）；版本卡下列 deck 紀錄（時間、狀態、閘門摘要）。
`JOB_REFRESH_TARGETS['ai:report_deck']=['reports']`。

**先看到、再下載（2026-08-12 使用者指定）**：完成後紀錄卡直接展示
**逐頁預覽**（就是產線目視那批 PNG，backend 自 artifact root 供圖）；
「下載 pptx」是使用者主動按的按鈕（backend 串流 NAS 上的檔，帶 manifest
hash 校驗），**不自動下載**。此為對「backend 不經手檔案流量」的修正：
不自動推送仍成立，但按需下載由 backend 供檔（NAS 掛載在伺服器上，順路）。

## Test Strategy

- runner 單元：機械步編排（subprocess 以假腳本樁測順序與失敗短路）、
  manifest 形狀、失敗不落 ROOT。
- 契約：AI_JOB_TYPES 收錄、前端 mapping（既有跨層測試自動強制）、
  按鈕與紀錄區字面契約。
- E2E（組合驗收）：真版本走完整鏈＋系統 vs 手工逐頁對照。

## Risks

- 撰稿品質退化（headless vs 互動）：Acceptance Gate 3 的對照驗收把關；
  check_content 的措辭／字數／佔位符閘門是底線。
- deck 一跑數分鐘：沿 narrative 的 keepalive／誠實進度模式回報 stage。
- 修正輪數上限：閘門紅 → runner 把 check_content 輸出回饋 CLI 重撰稿，
  達 `DECK_VISUAL_LOOP_MAX_ROUNDS`（產線參數，預設 4）即 failed
  ——上限是可調參數，不再綁 skill 開發紀律（2026-08-12 使用者定案）。
