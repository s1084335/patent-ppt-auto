# report-export（delta）

## REMOVED Requirements

### Requirement: PPT 交付線（規劃、組版、下載、預覽、編輯）

系統不再提供 PPT 產製與交付：`ai:report_plan`／`ai:report_ppt` 任務型別、
`build_ppt` 組版、PPT 端點（`/reports/ppt-layout`、`/reports/versions/{v}/ppt-files`、
`/report-latest/ppt/*`）、`ppt_eligible` 選圖標記與前端編輯模式一併移除
（2026-08-10 使用者定案；沿革與「原本要回答的問題現在由誰回答」見 proposal）。

#### Scenario: 建立 PPT 任務被明確拒絕

- **WHEN** 前端或 API 呼叫端以 `task_type: ai:report_plan` 或 `ai:report_ppt`
  呼叫 `POST /ai-tasks`
- **THEN** SHALL 回 422 `unsupported task_type`
- **AND** 不得建立任務、不得靜默成功

## ADDED Requirements

### Requirement: 解讀完成的 HTML 報表為交付物

系統 SHALL 以「報表產製 → AI 解讀 → 解讀嵌入 `index.html`」為交付主線；
報表種類頁版本區 SHALL 提供「匯出 HTML 檔」入口，產出**自包單檔**
（SVG 內嵌 data URI，離線可開）。

#### Scenario: 從報表種類頁匯出自包 HTML

- **WHEN** 使用者於版本區按「匯出 HTML 檔」
- **THEN** SHALL 下載該版本的單一 `.html` 檔
- **AND** 檔內所有圖表 SHALL 為內嵌 data URI，無外部資源引用
- **AND** 已產出的 AI 解讀 SHALL 隨卡呈現；未產出時卡片標示待產生

#### Scenario: 解讀契約檔隨 backend 部署

- **WHEN** `ai:narrative` 於任何部署環境執行
- **THEN** 解讀契約（flow／content_standard 節錄／data_access）SHALL 自
  `backend/app/worker/prompts/` 載入（可用 `REPORT_NARRATIVE_FLOW_PATH` 覆寫）
- **AND** 不得依賴已移除的 `skills/patent-report-ppt/`
