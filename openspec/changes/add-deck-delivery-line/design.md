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
  4. subprocess：check_content → make_deck → audit_deck（任一紅→job failed）
  5. 回存：pptx 寫 <DECK_ARTIFACT_ROOT>/<version>/，manifest＋紀錄寫 DB
```

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
payload=based_on_version）；版本卡下列 deck 紀錄（時間、狀態、NAS 相對
key、閘門摘要）。`JOB_REFRESH_TARGETS['ai:report_deck']=['reports']`。

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
- 兩輪修正上限：閘門紅 → runner 把 check_content 輸出回饋 CLI 重撰稿一次，
  仍紅即 failed（同 skill「同一問題最多修兩輪」既有規則）。
