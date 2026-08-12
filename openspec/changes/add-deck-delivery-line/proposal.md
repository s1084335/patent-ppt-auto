## Why

`html-report-to-deck` skill 已能把解讀完成的報表轉成可簡報的 PPTX（九步流程、
版面引擎、閘門、回歸基準俱全），但它是 agent 端手動工作流：使用者得叫 agent 跑。
2026-08-12 使用者定案把它接進系統成為產品能力——**盡量不動流程本體，
只把接口改成適合系統的形**（觸發、取料、產物回存、驗收閘門）。

## What Changes

- **新 AI job 型別 `ai:report_deck`**：前端報表種類頁版本區加「產製簡報」入口
  → 既有佇列 → Companion 領取 → deck runner。
- **runner 分工（接口設計核心）**：九步中僅第 5 步撰稿是 AI 判斷——
  **runner 以 subprocess 跑全部機械步**（intake→plan→chip 重排→fit→
  check_content→make_deck→audit），**CLI 只接撰稿**（輸入 plan.json＋素材、
  輸出 content.json）。CLI 權限面與 `ai:narrative` 同級：讀素材、寫一個 JSON，
  不需要 Bash 白名單擴張。
- **skill 遷入產品 repo** `skills/html-report-to-deck/`（中央 `.agents/skills/`
  份刪除，單一落點）：SKILL.md 依產品 skill 硬規範改寫（執行 Runbook／開發備註
  兩區）；開發機路徑全部參數化；原「不是產品交付線」邊界註記依 2026-08-12
  定案改寫留痕。
- **產物回存＝DB＋NAS（2026-08-12 使用者定案）**：DB 記產製紀錄與 manifest
  （based_on_version、參數、狀態、檔名、SHA-256、**NAS 相對 key**）；pptx 本體
  寫 NAS（現階段本機目錄＋環境變數代替，沿 `MODEL_ARTIFACT_ROOT` 前例）。
  **不做自動下載**：前端只顯示紀錄與 NAS 位置，backend 不經手檔案流量。
- **前端**：版本區「產製簡報」按鈕＋deck 紀錄清單（時間、版本、NAS 位置、狀態）；
  `JOB_REFRESH_TARGETS` 補 `ai:report_deck`（跨層對帳測試強制）。

## 已確認決策（本 change 的邊界）

1. 流程本體不動：`plan_deck`／`fit_render_charts`／`deck_layout`／`check_content`
   ／`make_deck`／`audit_deck` 與其閘門、授權界線（僅 chip 四象限可重排）、
   `regression.py` 像素基準全部原樣。
2. 取料走 `unify-chart-source` 的版本目錄 intake（該 change 先行）。
3. 產物 DB＋NAS、不自動下載（2026-08-12）。
4. AI 層將移伺服器（Companion＋CLI 集中，2026-08-12）——runner 設計必須
   host-agnostic：不假設 Windows、不依賴使用者桌面。
5. Installer 已廢止——skill 佈署屬伺服器佈署腳本範圍，不做使用者端打包。

## 未決問題（規劃確認時請裁決）

- **Q1 視覺驗收步的產品線定位**：`pptx_to_png`（PowerPoint COM）在 Linux
  伺服器不可行。**建議**：產品線以確定性閘門為準（check_content＋make_deck
  裕度 0 溢出＋audit_deck＋圖內字級門檻），COM 逐頁目視留在 skill 開發側
  （regression 像素基準守版面回歸）；deck 紀錄標註「未經逐頁目視」。
  替代選項：伺服器選 Windows 機（成本高）／LibreOffice 轉圖（已因保真度否決過）。

## Capabilities

### New Capabilities

無（掛既有 capability）。

### Modified Capabilities

- `report-export`：新增簡報產製與回存契約。
- `ai-companion`：新增 `ai:report_deck` 派工與 CLI 撰稿契約。

## Scope

`backend/app/worker/ai_report_deck_runner.py`（新）、`job_repository.AI_JOB_TYPES`、
ai_bridge 派工表、deck artifact 落點（env root＋manifest 寫 DB）、前端
（按鈕＋紀錄區＋刷新 mapping）、`skills/html-report-to-deck/`（遷入＋硬規範
改寫）、對應測試。

## Non-goals

- 不改九步流程的任何演算法、版面、閘門門檻。
- 不做自動下載、不做瀏覽器內預覽 pptx。
- 不在本 change 執行「Companion 上伺服器」的搬遷（runner 只需 host-agnostic，
  搬遷屬部署工作）。
- 不動 `ai:narrative` 線。

## 相依

`unify-chart-source`（intake 前置）先實作並驗收。

## Acceptance Gate

1. 前端按「產製簡報」→ job 入佇列 → Companion 領取 → runner 機械步全跑
   → CLI 撰稿 → 閘門全綠 → pptx 落 NAS root、DB 有紀錄與 manifest
   （hash 與檔案相符）→ 前端紀錄區自動出現（SSE）。
2. 確定性閘門逐項：check_content 過、make_deck「溢出區域：0 個」、
   audit_deck exit 0、單圖頁圖內字級 ≥9pt 清單列出。
3. **系統產 vs 手工產逐頁對照一次**（同一版本各產一份）——撰稿品質不得
   低於手工線；差異列出交使用者判。
4. 失敗路徑：CLI 撰稿超時／閘門紅 → job failed 帶原因，無半成品落 NAS。
5. skill 硬規範稽核（Runbook 區零開發機路徑）＋`check_docs.py`＋
   `regression.py` 全綠；中央舊份已刪、引用已改。
6. 範圍回歸＋OpenSpec strict。
