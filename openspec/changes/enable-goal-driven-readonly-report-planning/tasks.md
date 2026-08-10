## 1. 基準、契約與相依收斂

- [ ] 1.1 固定代表性 workspace、analysis snapshot、使用者選圖集合、兩份參考範例、現行 `report_data/narratives/PPTX` 與全頁 PNG 基準　（未做：無重產前基準——goal-driven 產出在此之前不存在）
- [x] 1.2 定義 `ReportBrief`、`SelectedChartBundle`、`ReportStrategy`、`SlidePlan`、`EvidenceManifest`、tool audit 與 manifest JSON schema，包含版本、checksum、容量與錯誤契約
- [x] 1.3 定義 `PptQualityReport`、`RenderedPngManifest`、`RegenerationPlan` 與 scope lock JSON schema，包含 warning 對應、decision enum、retry 上限與 blocked defect 分類
- [ ] 1.4 建立與 `improve-report-professionalism`、`complete-export-report-editing`、`harden-runtime-security-and-configuration` 的實作順序表，確認固定頁序／固定 slots 不會先被實作後再拆除　（未做：實作順序表；實際順序已由本輪 commit 序決定）

## 2. TDD：選圖資料包與最大目標

- [x] 2.1 Red：新增全部選圖圖片＋數據成對、snapshot/checksum 一致、空選圖、重複 identity、遺漏選圖與未選圖引用測試，實際執行並記錄 Red 原因
- [x] 2.2 Green：最小完成 ReportBrief 與 immutable selected-chart bundle producer/materializer，使資料包契約測試通過
- [x] 2.3 Red：新增最大目標、章節 constraints、page budget、全部選圖至少一次、缺圖建議不得進正式 plan 的驗證測試
- [x] 2.4 Green：完成 plan schema validator 與明確錯誤回應，不接 CLI 或 builder

## 3. TDD：唯讀 MCP 與 DB 權限

- [x] 3.1 Red：新增 report-research tool registry 精確白名單測試，確認 save/refresh/generate/apply/shell/filesystem-write 均不可見
- [x] 3.2 Green：建立獨立 read-only MCP profile 與 typed catalog/evidence query broker，沿用 `REPORT_DEFINITIONS` 與 snapshot scope，不接受 SQL 字串
- [x] 3.3 Red：新增欄位/filter/snapshot/row-limit/timeout、跨 workspace、stale evidence 與 query audit 測試　（2026-08-09 補齊 query audit：逐次記工具／snapshot／列數／截斷／錯誤，經環境變數指定的 JSONL 跨行程傳回 runner result）
- [x] 3.4 Green：完成 preview/query/company/topic/patent evidence 工具與分頁／截斷 metadata
- [ ] 3.5（移出範圍）DB reader role：2026-08-07 使用者裁決不做——正式部署為公司內網自管伺服器、CLI 依架構不持有 credential，維運成本大於邊際效益。改以「CLI 不得持有 DB credential」契約測試守（見 PRT-012 回寫）。
- [x] 3.6 Green：MCP config 與 payload 不含 credential；工具層 allowlist contract test。

## 4. TDD：CLI 規劃與證據驗證

- [x] 4.1 Red：以 fake CLI/MCP 新增圖片與結構化數據全量傳入、工具 budget、provider capability、無工具退化與結構化 response 測試
- [x] 4.2 Green：新增 goal-driven report planning job/runner、隔離 MCP config、受控 payload 目錄與 provider fail-closed
- [x] 4.3 Red：新增數字／具名對象 evidence、snapshot/version、chart coverage、未選圖、任意 geometry、補查結果新圖化與空資料限制測試
- [x] 4.4 Green：完成 strategy/slide/evidence validation；只由 runner 保存驗證後候選 artifact，CLI 無 DB/artifact write tool
- [x] 4.5 Refactor：全綠後共用既有 CLI payload/JSON extraction/artifact store，不複製 report registry、版型或 credential 邏輯

## 5. TDD：動態組版與前端流程

- [x] 5.1 Red：新增 `plan_id + slide_id`、layout preset resolution、合頁／拆頁、全部選圖覆蓋、容量與 deterministic rebuild 測試
- [x] 5.2 Green：讓 portable builder 消費通過驗證的 SlidePlan，以有限核准 presets 組版並輸出 chart/evidence/goal manifest
- [x] 5.3 Red：新增前端最大目標、章節方向、選圖提交、規劃進度、缺圖建議、失敗訊息與 preview readback 測試　（2026-08-09 A4 實測完整流程：選取集合→CLI→SlidePlan→成品六項判準逐項核對通過）
- [x] 5.4 Green：完成 ReportBrief UI/API 與既有報告 runner fallback/feature flag；不讓使用者未選圖自動進 PPT

## 6. TDD：產後品質 gate 與局部重產

### 第 6 節的分工與相依（2026-08-10，合併後更新）

多 agent 並行，邊界如下；⚠ **同一工作樹**，已實際互相覆蓋過三次
（checkout、工作紀錄檔、回歸讀到改到一半的版本），交接前先確認對方停手。

| 項 | 執行者 | 分支 | 狀態 |
|---|---|---|---|
| 1.3 schema ＋ 6.1／6.2 `PptQualityReport` | Codex | `feat/ppt-quality-report` | ✅ 完成（`b6269f4`），已合入報表分支 |
| `improve-report-professionalism` 3.1 `verify_module` preset | Codex | 同上 | ✅ 完成，已合入 |
| 6.3–6.5 局部重產 ＋ scope lock | Claude | `feat/improve-report-catalog` | 相依已解除（schema 到位），可動工 |
| 7.5／7.6 驗收 | Claude | 同上 | 等 6.x 與 PPT 實物驗收 |

⚠ **合併注意**：兩邊都改 `planning_contracts.py`——Codex 加 quality report schema、
Claude 加四道規劃閘門（`validate_research_effort`／narrative 溯源／數字校驗）。
兩者函式不重疊，git 自動合併成功；合併後須跑雙方目標測試確認語意也相容，
不能只看「沒有衝突標記」就當作整合完成。

⚠ **本節的前提在 2026-08-10 才剛補上**：`ai:report_plan` 產出的 SlidePlan 原本
**沒有回存 DB**，下游 `ai:report_ppt` 從 DB materialize 拿到沒有 plan 的版本，
`resolve_layout` 靜默退回固定頁序——實測 11 頁的規劃變成 14 頁固定頁序，
Key Player 象限圖整個沒進 PPT，且**全程無錯誤訊息**。已修（`8d20c39`），
契約由 `tests/test_slide_plan_reaches_ppt.py` 守。

沒有這個修正，第 6 節的 quality report 會一直在評估「固定頁序的產物」，
卻以為自己在評估 goal-driven 的產物——量到的每個數字都是對的，結論卻全錯。

- [x] 6.1 Red：新增 manifest warnings 到 quality decision 的契約測試，涵蓋 `narrative_missing`、`narrative_fallback`、`chart_missing_degraded`、`artifact_manifest_missing`、`missing_slots`、`text_overflow_estimated`、`text_overlap`、`out_of_bounds`、PNG render 失敗與頁數不符
- [x] 6.2 Green：完成 `PptQualityReport` 產生器，彙整 PPTX manifest、RenderedPngManifest、選圖覆蓋、evidence coverage、必要 slot 與版面 warnings
- [ ] 6.3 Red：新增 `RegenerationPlan` scope lock 測試，確認 CLI 只可回傳指定 targets，改動 locked slide、chart identity、未標記 narrative 或未選圖表時必須拒收
- [ ] 6.4 Green：完成局部重產 runner 接線，保留未標記 narratives/slides，保存 replacement audit，並在重產後重新 build、轉 PNG、跑 quality report
- [ ] 6.5 Red：新增同一 target 超過兩輪仍 fail 的測試，確認停止自動重產並標示 `blocked_content_defect` 或 `blocked_layout_defect`
- [ ] 6.6 Refactor：共用既有 PPT manifest warning 與 narrative 單報表重產規則，避免在 runner 與 skill 文件複製兩份不一致 mapping

## 7. 整合、實物與安全驗收

- [x] 7.1 執行 MCP/report/AI runner/PPT builder/frontend 目標測試、受影響回歸與 `scripts/verify_module.py`，保存 Red/Green/Refactor 證據　（2026-08-09 A5：功能測試通過、新增行 lint 0、本輪新增／改寫 33 支函式 CC 全 ≤ B、新增行覆蓋率 87%；未覆蓋 18 行全為需真 DB／真 CLI 的路徑，已由 A4／A6 實測腳本驗證）
- [x] 7.2 在隔離 DB 及正式部署等價設定驗證 reader role 權限矩陣、secret redaction、statement timeout、row limit、跨 workspace/snapshot 拒絕與 rollback　（2026-08-09 A6 實測：抓到唯讀護欄從未生效——pooler 忽略 startup options，UPDATE／CREATE／DELETE 全過。改綁交易層後四項判準全通過）
- [x] 7.3 以真 CLI 讀取全部選圖圖片／數據並迭代補證據，核對 tool audit、EvidenceManifest、數字與具名主張；不得只用 fake CLI 宣告完成　（2026-08-09 A4 實測：真 CLI 跑完整流程，query_audit 跨行程回傳 4 筆呼叫紀錄，0 筆失敗）
- [x] 7.4 產生完整 PPTX、report strategy、slide plan、evidence manifest、artifact manifest、PptQualityReport、RegenerationPlan（若觸發）與全頁 PNG，程式化掃描全部頁後逐頁人工檢查目標論證、選圖完整、截字、重疊與空白圖　（2026-08-09 A4 實測：成品 10 頁＝plan 10 頁，零 warning；逐頁轉圖檢視）
- [ ] 7.5 以刻意缺 narrative、缺圖、文字溢位與 locked slide 被 CLI 改動的案例驗證局部重產 gate，證明未標記內容未改、重產後重新驗收，且超過重試上限會 blocked
- [ ] 7.6 與現行固定 runner shadow compare，揭露未驗項目與 rollback；使用者接受內容、視覺、quality gate 與唯讀證據後才啟用預設路徑或 archive
