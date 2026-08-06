# Patent Reporting Design

## 架構與資料流

`REPORT_DEFINITIONS -> report_engine -> chart_runner -> report_data/index/charts -> report_artifacts`。SQL 型報表由白名單 query builder 執行；cluster 型報表由 finalized topic assignments 與 derived patent data 組成。Narrative 以 report/variant key 掛回 section，不由 PPT 端重算數據。

現行 registry 包含趨勢、國家、IPC/CPC、申請人／權人排名與年度矩陣、生命週期、家族品質、主題統計與機會四象限等定義。實際啟用與未來刪改以 registry 為唯一現況，不以舊文件硬寫數量。

## 程式落點

- Registry：`backend/app/reports/report_definitions.py`
- SQL：`backend/app/reports/report_engine.py`
- 圖表與 HTML：`backend/app/reports/chart_runner.py`
- 分群分析：`backend/app/reports/cluster_analytics.py`
- 母體註記：`backend/app/reports/population.py`
- API／版本：`backend/app/api/reports.py`、`backend/app/main.py`
- Artifact：`backend/app/db/report_artifact_store.py`

## 測試證據

- `tests/test_report_engine_aggregates.py`、`test_report_engine_family.py`
- `tests/test_report_analysis_types.py`
- `tests/test_report_quality_and_ipc_filter.py`
- `tests/test_report_types_frontend_backend_parity.py`
- `tests/test_cluster_reports_*.py`
- `tests/test_chart_sections.py`
- `tests/test_population_note.py`、`tests/test_footnote_population.py`
- `tests/test_report_artifact_store.py`
- `tests/test_api_report_versions.py`、`tests/test_api_report_content.py`
- `tests/test_ai_narrative_runner.py`、`tests/test_narrative_*.py`

## 輸出契約

每個版本可包含 `report_data.json`、圖表 SVG/PNG、`index.html`、section metadata、`narratives.json` 與版本 metadata。跨容器需要的檔案存入 `report_artifacts`；local cache 可被清除並由 DB materialize。

## 驗收邊界

單元／契約測試不代表正式 DB projection 已更新。涉及 schema 或來源欄位的變更，需另跑 migration、derived refresh、代表查詢與受影響報表 smoke。

