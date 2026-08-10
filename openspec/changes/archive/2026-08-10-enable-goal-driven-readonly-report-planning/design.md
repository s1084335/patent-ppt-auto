## Context

現行 `ai_report_ppt_runner` 將完整 `report_data.json`、`narratives.json` 與固定 slots 寫入 payload file，CLI 只有 `Read`，再由 deterministic `build_ppt.py` 組版。現有 Patent MCP 已有 `list_reports`／`run_report_analysis`，但同一 server 也註冊 save、refresh、generate 與治理寫入工具，不能直接交給新的報告規劃工作。

## Goals / Non-Goals

**Goals:** 讓 CLI 看見全部使用者選圖與數據，以最大目標形成動態論證，並能在相同 snapshot 內唯讀補證據；以工具、DB、輸出驗證三層限制權限與幻覺。

**Non-Goals:** 不提供 raw SQL；不讓補查資料變成新圖；不把 PowerPoint 幾何交給模型；不讓 CLI 直接保存 DB／artifact；不以範例固定整份頁序。

## Decisions

### 1. ReportBrief 與 SelectedChartBundle 是唯一任務輸入

`ReportBrief` 保存 `north_star_goal`、audience、chapter constraints、directions、page budget、workspace/analysis snapshot 與 selected chart identities。每個 `SelectedChartBundle` 項目同時帶 image path/materialized artifact、report-data slice、definition/filter/population metadata、version 與 checksum。

只給圖片會失去精確數字，只給 JSON 會失去視覺判讀；兩者必須成對且以 checksum/version 阻止錯配。圖片先由 runner 從 artifact store materialize 到受控唯讀工作目錄，CLI 只取得列入 manifest 的檔案。

### 2. 新增隔離的 report-research MCP profile，不重用混合 server

工具面只包含 catalog、preview/query report evidence、chart metadata、company/topic/patent evidence。每支工具消費 typed filters 與 snapshot identity，內部沿用 `REPORT_DEFINITIONS`／既有 repository，不接受 SQL 字串。

替代方案是從現有 MCP 隱藏寫入工具；但同一 registry 日後新增工具容易無聲擴權，因此使用獨立 server/profile 與 allowlist contract test。CLI 啟動使用隔離 MCP config，除 payload/image `Read` 外不開 shell 或 filesystem write。

### 3. DB identity 是第二道強制邊界

report-research server 使用獨立 reader credential，只取得必要 derived/report views 與明確證據 projection 的 SELECT/EXECUTE。connection 預設 read-only transaction，設定 statement timeout；工具另有列數、欄位與 workspace/snapshot 邊界。Alembic migration 管理 grants/撤銷，secret 由部署環境注入，不進 payload 或 CLI。

單靠 prompt 或 MCP registry 無法抵抗工具 bug；DB role 必須能獨立拒絕 DML/DDL。權限驗收直接嘗試 INSERT/UPDATE/DELETE/DDL/side-effect function 並確認全拒絕。

### 4. Agent loop 只產結構化候選

資料流：

```text
UI ReportBrief + selected charts
  -> runner materializes immutable bundle
  -> CLI reads all images/data
  -> read-only MCP evidence queries
  -> ReportStrategy + SlidePlan + EvidenceManifest
  -> runner validation
  -> deterministic builder
  -> post-build quality validation
  -> regeneration decision
  -> platform persists candidate artifacts
```

CLI response 不含任意 geometry，只含 slide purpose、title、chart identities、approved layout intent、narrative blocks、evidence refs 與 recommendation。runner 驗證全部選圖至少使用一次、沒有未選圖、evidence snapshot 一致、數字存在、版型可承載；失敗不組版。

### 4.1 產後品質 gate 由 runner 決定，不交給 CLI 自評

每次 build 後 runner MUST 彙整三類證據：

- `pptx_manifest.json`：builder 輸出的 slide identity、report keys、chart identities、missing slots、missing reports、warnings、degraded state 與 checksum。
- `rendered_png_manifest.json`：PowerPoint COM 轉圖結果、頁數、每頁 PNG path/checksum、render 成功或失敗。
- `ppt_quality_report.json`：由 runner 將 manifest warnings、PNG render、選圖覆蓋、evidence coverage、必要 slot 與版面警告合併後產生的 pass/fail 報告。

`PptQualityReport` 的 `decision` 只能是 `pass`、`regenerate_partial`、`regenerate_report_version` 或 `blocked_defect`。CLI 不能覆寫此 decision，也不能把失敗的 PPT 標成可交付。

Warning 對應規則：

- `narrative_missing`：`regenerate_partial`，target 為指定 `report_key` / variant 的 narrative。
- `narrative_fallback`：`regenerate_partial`，target 為指定 narrative，要求符合目前 narrative schema。
- `chart_missing_degraded`：`regenerate_partial` 或 `regenerate_report_version`，由 artifact identity 決定是單圖修復或整份報表版本重產。
- `artifact_manifest_missing`：`regenerate_report_version`。
- `missing_slots` 含必要 slot：`regenerate_partial`，target 為指定 slot。
- `text_overflow_estimated`：先以 `regenerate_partial` 要求縮短對應 slide narrative；同 target 重試仍失敗時轉為 `blocked_defect`。
- `text_overlap`、`out_of_bounds`、`margin_violation`：`blocked_defect`，視為 builder/theme/layout 缺陷，不交給 CLI 自由改版。
- 選圖未全部出現在 PPT、PPT 出現未選圖、evidence 缺來源、PNG render 失敗或頁數不符：fail，依可定位範圍產生局部重產或阻止交付。

`RegenerationPlan` MUST 明列 `targets` 與 `locked`。CLI 只可回傳被標記 target 的替換內容；未列入 target 的 narratives、slide purpose、chart identities 與 evidence refs 必須保持不變。runner 驗證輸出 scope 後才可再次組版。每個 target 預設最多兩輪局部重產；超過後標為 `blocked_content_defect` 或 `blocked_layout_defect`，停止自動重寫並回到人工／開發處理。

### 5. 動態 plan 使用穩定 slide identity

`slide_id` 在同一 `plan_id` 內穩定，頁碼只是 render 結果。builder 以 slide content shape 選核准 layout preset；合頁／拆頁由 plan 表達，幾何仍由 theme/layout library 決定。這讓歷史、草稿與單頁 candidate 可用 `plan_id + slide_id + revision_id` 追蹤，不依固定 `PAGE_LAYOUT` index。

### 6. 平台保存與 CLI 權限分離

CLI stdout 回傳候選；Companion runner 驗證後才透過既有 artifact store 保存。CLI 看不到 artifact/database write tool。這保留版本歷史與跨容器讀取，同時符合「CLI 沒有改寫和輸入資料庫權力」。

## Code And Data Boundaries

- Frontend/API：ReportBrief、選圖 identity、規劃狀態與預覽。
- Companion：新 job capability check、隔離 MCP config、payload/image materialization、response validation。
- Reporting：selected bundle producer、evidence catalog/query broker、snapshot enforcement。
- MCP/runtime：read-only tool registry、reader connection settings、audit/redaction。
- Portable PPT skill：SlidePlan schema consumer、layout preset resolver、manifest/coverage/capacity validation。
- DB migration：reader role/grants 或可重現 grant script；不搬資料、不改核心欄位。

## Output And Test Evidence

- 契約產物：`report_brief.json`、`selected_chart_manifest.json`、`report_strategy.json`、`slide_plan.json`、`evidence_manifest.json`、tool audit、PPTX manifest。
- 產後驗證產物：`rendered_png_manifest.json`、`ppt_quality_report.json`、`regeneration_plan.json`、局部重產 scope audit。
- 單元／契約：全部選圖覆蓋、未選圖拒絕、snapshot/checksum、數字 evidence、provider capability、tool registry 白名單、DB grants。
- 整合：真 reader role 的 SELECT 成功與所有寫入失敗；timeout/row limit；CLI 圖片＋JSON＋迭代查詢。
- 實物：固定 snapshot 產完整 PPTX，全頁轉圖，逐頁核對最大目標、選圖、敘述證據與版面；若 quality gate 觸發局部重產，需證明未標記內容未被改動，重產後重新 build、轉圖並產生新的 quality report。

## Risks / Trade-offs

- [CLI 查詢過多或循環] → 工具呼叫 budget、timeout、row limit、重複 query cache 與 audit。
- [圖片被傳入但模型未真正使用] → chart coverage、每頁 purpose/evidence、tool/image read trace 與人工逐頁驗收共同守門。
- [動態頁序讓版型組合爆增] → 只允許有限 content-shape presets，未知 shape fail loud，不接受任意 geometry。
- [補查結果與選圖版本不同] → 所有工具強制 snapshot identity，stale response 不進 EvidenceManifest。
- [reader role 因 grant 漂移而擴權] → migration contract、catalog allowlist 與真 DB negative permission tests。
- [CLI provider 不支援受控 MCP 或圖片] → capability check 後 fail closed，不退回 shell/full-access 模式。

## Migration Plan

1. 建立 schema/contracts 與 fake tool gateway；保留現有固定 runner fallback。
2. 建立 reader role/grants、read-only MCP profile 與 negative permission tests。
3. 建立 selected chart bundle、ReportBrief 與 CLI planning runner。
4. 接 SlidePlan validator、builder presets、manifest 與前端預覽。
5. 以 feature flag 對代表 workspace shadow run，比對既有輸出後才切換。
6. Rollback 關閉 feature flag、撤銷 reader credential/profile；既有 report data 與 artifacts 不需搬移或刪除。
