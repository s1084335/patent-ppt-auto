# PPT 交付線（已停產）

2026-08-20 使用者定案：**不再產出 PPT，最終交付檔案為 HTML**。
報表版面改由外部設計者重新設計，本目錄保存 PPT 交付線的殘餘文件供追溯。

## 退場歷程

| 日期 | 事件 |
|---|---|
| 2026-08-10 | `remove-ppt-delivery-line`：後端 `ai:report_ppt`／`ai:report_plan` job type、`/reports/versions/{v}/ppt-files`、`/report-latest/ppt`、`/workspaces/{id}/report-plan` 端點全部移除 |
| 2026-08-11 | 該 change 封存；`openspec/changes/archive/2026-08-11-remove-ppt-delivery-line/` |
| 2026-08-20 | **前端「匯出報告」工作台整塊移除**（本次）：後端端點拔掉後，前端仍留著整套打 404 的死碼近十天 |

⚠ 教訓：後端拔端點時前端沒同步，而**沒有任何測試守著**，所以十天無人察覺。
本次補上 `tests/test_api_frontend.py::test_export_workbench_and_ppt_removed`，
同時守「工作台不得回來」與「報表種類頁不得被波及」。

## 本次移除的內容

- 前端 32 支函式（工作台 ＋ PPT 專用），約 710 行
- `.real-pptx-preview` 系列 CSS（7 條）、`.ppt-chart`、`.report-ppt-*`（6 條）
- `backend/app/static/vendor/pptx-renderer/aiden0z-pptx-renderer.browser.es.js`（1.52 MB）
- 已失效的前端測試 15 支

## 保留下來的（未受影響）

交付主線＝**報表產製 → AI 解讀 → 解讀嵌入 HTML → 匯出自包單檔**：

- 報表種類頁版本區的「匯出 HTML 檔」入口（`exportReportHtmlFile`，SVG 以 data URI 內嵌）
- 內嵌報表渲染 `renderReportContentHtml`／`readOnlyReportView`
- `ai:narrative` 解讀線全部

## 檔案

- `ppt_skill_input_contract.md`：舊 PPT skill 的輸入契約。

⚠ 本目錄內容不得被正式後端、正式測試或部署流程 import。
