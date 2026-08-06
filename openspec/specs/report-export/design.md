# Report Export Design

## 架構與資料流

`report version -> ai:report_ppt -> approvals/narrative slots -> build_ppt.py -> .pptx -> report_artifacts -> preview/download API`。`build_ppt.py` 依 `PAGE_LAYOUT`、theme 與 report metadata 決定頁面；AI runner 不直接操作 python-pptx 幾何。

前端「報表種類」是單份分析視角；「匯出報告」以實際 PPT 頁為單位。真實預覽使用 vendored pptx renderer；PowerPoint COM 轉圖用於驗內容、顏色、元素與圖表，字級／斷行仍需實機複驗。

## 程式落點

- Skill：`skills/patent-report-ppt/SKILL.md`
- 組版：`skills/patent-report-ppt/scripts/build_ppt.py`
- Theme：`skills/patent-report-ppt/theme/`
- AI runner：`backend/app/worker/ai_report_ppt_runner.py`
- 版本／下載／preview：`backend/app/main.py`、`backend/app/static/index.html`
- Artifact：`backend/app/db/report_artifact_store.py`

## 測試證據

- `tests/test_ppt_builder.py`
- `tests/test_ppt_builder_dynamic_pages.py`
- `tests/test_ppt_layout_contract.py`
- `tests/test_ppt_no_truncated_text.py`
- `tests/test_ppt_reader_facing_output.py`
- `tests/test_ppt_geometry_single_source.py`
- `tests/test_ai_report_ppt.py`
- `tests/test_export_report_wiring.py`
- `tests/test_export_html_preview_tab.py`
- `tests/test_api_report_ppt_*.py`

## 輸出契約

正式輸出至少包括 `.pptx` 與可追溯版本 metadata；依流程可附 `approvals.json`、`narratives.json`、`report_data.json`、layout metadata、charts 與 preview assets。不得把只存在於 Companion 本機的檔案視為交付完成。

## 已知未完成

報告專業度新一輪改版與 0046/A5 正式資料重產仍是 active changes；main spec 只描述目前已落地的輸出管線。

