## Why

`html-report-to-deck` skill 已能把解讀完成的報表轉成可簡報的 PPTX（九步流程、
版面引擎、閘門、回歸基準俱全），但它是 agent 端手動工作流：使用者得叫 agent 跑。
2026-08-12 使用者定案把它接進系統成為產品能力——**盡量不動流程本體，
只把接口改成適合系統的形**（觸發、取料、產物回存、驗收閘門）。

## What Changes

- **新 AI job 型別 `ai:report_deck`**：前端**「匯出報告」頁**加「產製簡報」入口
  → 既有佇列 → Companion 領取 → deck runner。
  （2026-08-12 使用者定案「匯出報告頁要接到 deck 去」：報表種類頁＝報表工作介面，
  匯出報告頁＝交付物中心。該頁現存的 PPT 線殘骸先清空，見 design §6／tasks 0。）
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
  **不自動下載，先看到再下載**（2026-08-12 使用者指定）：完成後前端先呈現
  逐頁預覽（產線目視同一批 PNG），使用者決定何時按「下載 pptx」
  （backend 自 NAS 按需串流）。
- **前端**：「匯出報告」頁放「產製簡報」按鈕＋deck 紀錄清單（時間、版本、狀態、
  逐頁預覽、下載鈕）；`JOB_REFRESH_TARGETS['ai:report_deck'] = ['export']`
  （跨層對帳測試強制）。前置＝清空該頁 PPT 線殘骸（含 1.5 MB pptx renderer
  vendor 與必定 422 的「產生 PPT」鈕）。

## 已確認決策（本 change 的邊界）

1. 流程與閘門不動、**組版輸出層改造（B 案，2026-08-12 使用者定案）**：
   `plan_deck`／`fit_render_charts`／`check_content`／`audit_deck` 與其閘門、
   `deck_layout` 幾何引擎、授權界線（僅 chip 四象限可重排）原樣；
   第 7 步組版改為「**每頁先組 SVG**（文字由引擎逐行斷好）→ Chromium 量測與
   截圖（目視內建、Linux 原生）→ **窄 SVG→DrawingML 轉換器**產原生 PPTX
   （逐行文字定位寫死、關 wrap——PowerPoint 零重排自由）」。
   借鑒 ppt-master 的「確定性中間層」概念；精準度**高於**現況
   （消滅 08-02 實證的 PowerPoint 斷行漂移），且產線不需任何 Windows。
2. 取料走 `unify-chart-source` 的版本目錄 intake（該 change 先行）。
3. 產物 DB＋NAS、不自動下載（2026-08-12）。
4. AI 層將移伺服器（Companion＋CLI 集中，2026-08-12）——runner 設計必須
   host-agnostic：不假設 Windows、不依賴使用者桌面。
5. Installer 已廢止——skill 佈署屬伺服器佈署腳本範圍，不做使用者端打包。
6. **內容架構吸收批次（2026-08-12 使用者逐項裁決，明細見 design §7）**：
   來源行（機械）／三欄分析帶（選項版型）／narrative 寫法範式（建議形）／
   插圖走**參數化圖形文法**六型（不建成品素材庫、第一版不開自由畫 SVG 後門）／
   **資料口徑頁**（引擎供定義原文＋數值，CLI 只做編排，定義逐字閘門）／
   低件數成因走**集中度指標**（引擎算、CLI 解讀）／**結論頁綜合版**
   （一主題一列，發現｜研發意涵｜專利行動；行動欄限有限動詞表）／
   逐案細讀清單**改版** roadmap 頁（不新增第二頁）。淨增一頁（口徑頁）。
   ⚠ 全批禁寫「什麼情況必須用什麼」的條件規則；**判斷留給 CLI 的前提是
   「資料裡有線索」**，外部事實（受眾、語氣）留給使用者。

## 未決問題

（無——原 Q1「視覺驗收定位」已於 2026-08-12 裁決為 B 案：目視改為產線內建的
Chromium 逐頁截圖（截 SVG＝截成品，因 PPTX 已無重排自由）；PowerPoint COM
降為**開發期一次性映射校驗**（Windows 開發機上證明「SVG 截圖＝PowerPoint 實開」，
逐頁型驗證後固定），不進產線。曾評估之替代案（Windows 轉圖代理／LibreOffice／
Aspose）的取捨留 design.md 備查。）

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

1. **映射校驗（B 案的地基，開發期、Windows 開發機）**：五種頁型逐一
   「Chromium 截 SVG vs PowerPoint COM 轉圖 vs 實機開檔」三方對照，
   版面（含斷行）一致才算映射成立；證據入 `output/_verify/`。
2. 前端按「產製簡報」→ job 入佇列 → Companion 領取 → runner 機械步全跑
   → CLI 撰稿 → 閘門全綠 → pptx＋逐頁目視 PNG 落 NAS root、DB 有紀錄與
   manifest（hash 與檔案相符）→ 前端紀錄區自動出現（SSE）＋逐頁預覽可看。
3. 確定性閘門逐項：check_content 過、組版裕度 0 溢出、audit_deck exit 0、
   單圖頁圖內字級 ≥9pt 清單列出。
4. **系統產 vs 手工產逐頁對照一次**（同一版本各產一份）——撰稿品質不得
   低於手工線；差異列出交使用者判。
5. 失敗路徑：CLI 撰稿超時／閘門紅 → job failed 帶原因，無半成品落 NAS。
6. skill 硬規範稽核（Runbook 區零開發機路徑）＋`check_docs.py`＋
   regression（基準改比 SVG 截圖）全綠；中央舊份已刪、引用已改。
7. 範圍回歸＋OpenSpec strict。
