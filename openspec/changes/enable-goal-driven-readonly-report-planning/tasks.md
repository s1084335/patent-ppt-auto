## 1. 基準、契約與相依收斂

- [ ] 1.1 固定代表性 workspace、analysis snapshot、使用者選圖集合、兩份參考範例、現行 `report_data/narratives/PPTX` 與全頁 PNG 基準
- [ ] 1.2 定義 `ReportBrief`、`SelectedChartBundle`、`ReportStrategy`、`SlidePlan`、`EvidenceManifest`、tool audit 與 manifest JSON schema，包含版本、checksum、容量與錯誤契約
- [ ] 1.3 定義 `PptQualityReport`、`RenderedPngManifest`、`RegenerationPlan` 與 scope lock JSON schema，包含 warning 對應、decision enum、retry 上限與 blocked defect 分類
- [ ] 1.4 建立與 `improve-report-professionalism`、`complete-export-report-editing`、`harden-runtime-security-and-configuration` 的實作順序表，確認固定頁序／固定 slots 不會先被實作後再拆除

## 2. TDD：選圖資料包與最大目標

- [ ] 2.1 Red：新增全部選圖圖片＋數據成對、snapshot/checksum 一致、空選圖、重複 identity、遺漏選圖與未選圖引用測試，實際執行並記錄 Red 原因
- [ ] 2.2 Green：最小完成 ReportBrief 與 immutable selected-chart bundle producer/materializer，使資料包契約測試通過
- [ ] 2.3 Red：新增最大目標、章節 constraints、page budget、全部選圖至少一次、缺圖建議不得進正式 plan 的驗證測試
- [ ] 2.4 Green：完成 plan schema validator 與明確錯誤回應，不接 CLI 或 builder

## 3. TDD：唯讀 MCP 與 DB 權限

- [ ] 3.1 Red：新增 report-research tool registry 精確白名單測試，確認 save/refresh/generate/apply/shell/filesystem-write 均不可見
- [ ] 3.2 Green：建立獨立 read-only MCP profile 與 typed catalog/evidence query broker，沿用 `REPORT_DEFINITIONS` 與 snapshot scope，不接受 SQL 字串
- [ ] 3.3 Red：新增欄位/filter/snapshot/row-limit/timeout、跨 workspace、stale evidence 與 query audit 測試
- [ ] 3.4 Green：完成 preview/query/company/topic/patent evidence 工具與分頁／截斷 metadata
- [ ] 3.5 Red：先建立 migration/grant 契約與真 DB negative tests，證明 SELECT 可用且 INSERT/UPDATE/DELETE/DDL/副作用函式全數失敗
- [ ] 3.6 Green：建立 reader role/grants、獨立 credential 設定與預設 read-only transaction；rollback 撤銷 grants/profile，不搬資料

## 4. TDD：CLI 規劃與證據驗證

- [ ] 4.1 Red：以 fake CLI/MCP 新增圖片與結構化數據全量傳入、工具 budget、provider capability、無工具退化與結構化 response 測試
- [ ] 4.2 Green：新增 goal-driven report planning job/runner、隔離 MCP config、受控 payload 目錄與 provider fail-closed
- [ ] 4.3 Red：新增數字／具名對象 evidence、snapshot/version、chart coverage、未選圖、任意 geometry、補查結果新圖化與空資料限制測試
- [ ] 4.4 Green：完成 strategy/slide/evidence validation；只由 runner 保存驗證後候選 artifact，CLI 無 DB/artifact write tool
- [ ] 4.5 Refactor：全綠後共用既有 CLI payload/JSON extraction/artifact store，不複製 report registry、版型或 credential 邏輯

## 5. TDD：動態組版與前端流程

- [ ] 5.1 Red：新增 `plan_id + slide_id`、layout preset resolution、合頁／拆頁、全部選圖覆蓋、容量與 deterministic rebuild 測試
- [ ] 5.2 Green：讓 portable builder 消費通過驗證的 SlidePlan，以有限核准 presets 組版並輸出 chart/evidence/goal manifest
- [ ] 5.3 Red：新增前端最大目標、章節方向、選圖提交、規劃進度、缺圖建議、失敗訊息與 preview readback 測試
- [ ] 5.4 Green：完成 ReportBrief UI/API 與既有報告 runner fallback/feature flag；不讓使用者未選圖自動進 PPT

## 6. TDD：產後品質 gate 與局部重產

- [ ] 6.1 Red：新增 manifest warnings 到 quality decision 的契約測試，涵蓋 `narrative_missing`、`narrative_fallback`、`chart_missing_degraded`、`artifact_manifest_missing`、`missing_slots`、`text_overflow_estimated`、`text_overlap`、`out_of_bounds`、PNG render 失敗與頁數不符
- [ ] 6.2 Green：完成 `PptQualityReport` 產生器，彙整 PPTX manifest、RenderedPngManifest、選圖覆蓋、evidence coverage、必要 slot 與版面 warnings
- [ ] 6.3 Red：新增 `RegenerationPlan` scope lock 測試，確認 CLI 只可回傳指定 targets，改動 locked slide、chart identity、未標記 narrative 或未選圖表時必須拒收
- [ ] 6.4 Green：完成局部重產 runner 接線，保留未標記 narratives/slides，保存 replacement audit，並在重產後重新 build、轉 PNG、跑 quality report
- [ ] 6.5 Red：新增同一 target 超過兩輪仍 fail 的測試，確認停止自動重產並標示 `blocked_content_defect` 或 `blocked_layout_defect`
- [ ] 6.6 Refactor：共用既有 PPT manifest warning 與 narrative 單報表重產規則，避免在 runner 與 skill 文件複製兩份不一致 mapping

## 7. 整合、實物與安全驗收

- [ ] 7.1 執行 MCP/report/AI runner/PPT builder/frontend 目標測試、受影響回歸與 `scripts/verify_module.py`，保存 Red/Green/Refactor 證據
- [ ] 7.2 在隔離 DB 及正式部署等價設定驗證 reader role 權限矩陣、secret redaction、statement timeout、row limit、跨 workspace/snapshot 拒絕與 rollback
- [ ] 7.3 以真 CLI 讀取全部選圖圖片／數據並迭代補證據，核對 tool audit、EvidenceManifest、數字與具名主張；不得只用 fake CLI 宣告完成
- [ ] 7.4 產生完整 PPTX、report strategy、slide plan、evidence manifest、artifact manifest、PptQualityReport、RegenerationPlan（若觸發）與全頁 PNG，程式化掃描全部頁後逐頁人工檢查目標論證、選圖完整、截字、重疊與空白圖
- [ ] 7.5 以刻意缺 narrative、缺圖、文字溢位與 locked slide 被 CLI 改動的案例驗證局部重產 gate，證明未標記內容未改、重產後重新驗收，且超過重試上限會 blocked
- [ ] 7.6 與現行固定 runner shadow compare，揭露未驗項目與 rollback；使用者接受內容、視覺、quality gate 與唯讀證據後才啟用預設路徑或 archive
