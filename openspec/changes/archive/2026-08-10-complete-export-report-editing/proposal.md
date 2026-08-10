> 🔴 **2026-08-10 作廢（未完成即封存）**：使用者定案改向——PPT 交付線整體移除，
> 交付物改為解讀完成的 HTML 報表。本 change 的主題（PPT 規劃／組版／匯出編輯）
> 隨之失去標的。停止時做到哪、為何停，見
> `openspec/changes/remove-ppt-delivery-line/proposal.md` 與
> `.agents`（中央）work-log `2026-08-10.md`。程式已自 repo 移除，git 歷史可取回。
## Why

完整 PPT 產生、預覽與下載主線已完成，但匯出頁仍缺歷史 PPT／狀態提示收斂、編輯稿持久化、HTML theme 對齊，以及「只重產一頁 AI 文案、對比後才覆蓋」流程。若不補齊，使用者編輯與局部重產仍不可追溯或不可逆。

## What Changes

- 以 workspace、report version 與 PPT artifact 列出歷史輸出、狀態、缺漏與分群版本提醒。
- 將使用者編輯稿與核准覆寫持久化，重新整理或跨程序後仍可依 `plan_id + slide_id` 讀回。
- 單頁 HTML 匯出消費 `theme.json`／共用版面資料，不維護第三套寫死樣式。
- 提供單頁重產候選：AI 只重產 goal-driven SlidePlan 中該 slide 的文案／版型意圖，沿用原選圖與 evidence bundle，整份 PPT deterministic rebuild；先並列比較，使用者確認後才覆蓋目標版本。
- 保存重產來源、候選、核准者、時間與前一版追溯資訊。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `report-export`: 增加歷史輸出、編輯稿持久化、theme-consistent HTML、單頁候選重產、對比核准與版本追溯契約。

## Scope

承接 `export-report-flow-spec.md` 真正未完成的批次 3–5；goal-driven 初次規劃由 `enable-goal-driven-readonly-report-planning` 負責，內容專業度由 `improve-report-professionalism` 負責。

## Non-goals

- 不恢復 HTML/CSS 模擬 PPT 預覽。
- 不讓使用者拖曳任意文字框、修改 chart identity 或直接破壞版型結構。
- 不以單頁操作跳過整份 PPTX 結構與 artifact 驗證。

## Impact

- 前端匯出頁、report/PPT API、artifact store、AI report runner 與 portable PPT skill。
- 可能需要新增 edit/revision metadata schema 或版本化 artifact 命名。
- 測試涵蓋跨 workspace、跨版本、重整讀回、候選比較、核准／取消與完整 PPT 產物。

## Activation

先上歷史／持久化讀寫，再啟用單頁候選；既有整份輸出路徑保留為回退。資料 migration 必須先保留現有 artifact。

## Acceptance Gate

以一份真實 goal-driven PPT 完成編輯、重整讀回、單頁重產、並排比較、取消、確認覆蓋與歷史回復；實際轉圖逐頁確認只有指定 `slide_id` 內容改變，全部選圖與 evidence references 仍完整，使用者驗收後才 archive。
