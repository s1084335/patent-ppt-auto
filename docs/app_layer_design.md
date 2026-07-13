# App Layer Design

`app_layer` 定位為任務執行、前端查詢、報表計算、AI 文字生成、檔案匯出與匯出追溯層。

此層不保存核心專利資料，不回寫 `raw_layer`、`core_layer`，也不污染 `derived_layer.report_patent_base`。

## Target Flow

```text
analysis_id
  ↓
read derived_layer.report_patent_base
  ↓
generate charts / summary / PPT / Excel / HTML
  ↓
write export_runs
  ↓
output files are traceable
```

## Tables

欄位採「輕量且正式」精簡版：追溯必要的留，投機預留的砍。第一版建前三張表，`frontend_query_logs` 暫不建立。

### app_layer.analysis_runs（10 欄）

用途：記錄一次分析任務的執行狀態、篩選條件、使用者參數與選入的專利集合快照。

```text
analysis_id                 主鍵
analysis_name               任務名稱
analysis_type               report / infringement / patentability
status                      pending / running / completed / failed
filter_json                 篩選條件快照
parameters_json             參數（ranking_limit、ipc_levels、family_dedup_mode…）
selected_patent_ids_json    選入 patent_id 陣列快照（重演範圍的關鍵）
error_message               失敗原因（全流程唯一錯誤欄）
created_at                  建立＝開始時間
completed_at                結束時間
```

砍掉：`source_schema` / `source_table`（第一版來源固定 `derived_layer.report_patent_base`）、`family_dedup_mode`（併入 `parameters_json`）、`requested_by`、`frontend_session_id`、`started_at`、`updated_at`。

### app_layer.analysis_outputs（8 欄）

用途：保存一次分析任務產生的圖表資料、統計結果或 AI 摘要文字。

```text
output_id           主鍵
analysis_id         FK → analysis_runs
output_type         chart_data / statistics / ai_summary / claim_comparison
output_name         例 applicant_ranking
result_json         結果本體
ai_model            AI 產出時記模型（含供應商，如 anthropic/claude-...），非 AI 為 NULL
prompt_version      prompt 版本（非 AI 為 NULL）
created_at          建立時間
```

砍掉：`source_query_json`（查詢定義已版本化在 `report_definitions.py`，條件快照在 `analysis_runs.filter_json`）、`ai_provider`（併入 `ai_model`）、`prompt_hash`、`status`。

### app_layer.export_runs（7 欄）

用途：記錄檔案匯出與追溯資訊。檔案本體放檔案系統 / Docker volume，DB 只存 metadata、路徑與 hash。

```text
export_id           主鍵
analysis_id         FK → analysis_runs
export_type         report_html / chart_svg / excel / ppt
file_path           檔案路徑
file_hash           sha256 hex（正式追溯核心）
parameters_json     匯出參數（含未來模板資訊）
created_at          匯出時間
```

砍掉：`export_format`（副檔名可推）、`template_name` / `template_version`（無模板系統，先入 JSON）、`file_name`（path 可推）、`file_size`（hash 已能驗完整性）、`status`、`started_at`、`completed_at`、`error_message`、`created_by`。

### app_layer.frontend_query_logs（第一版不建）

預留給前端查詢追蹤，等前端查詢行為穩定後再加入。

## Rules

- `analysis_id` 是前端、後端 runner、AI summary、export 的共同追溯 key。
- `derived_layer` 提供可查詢母表，不保存每次任務最終結果。
- `app_layer` 保存任務狀態、輸出結果與匯出追溯。
- `analysis_outputs` / `export_runs` 只寫成功結果；失敗原因統一記在 `analysis_runs.error_message`。
- 可從其他欄位推得的資訊不存（`file_name`、`export_format` 由 `file_path` 推得）。
- AI 文字生成結果只能寫入 `analysis_outputs`，不能直接改 core patent data。
- PPT / Excel / PDF / HTML 檔案存放在檔案系統或 Docker volume，DB 保存 `file_path` 與 `file_hash`。
- Migration 仍由未來獨立 migrate container 執行，不由 app runner 或 worker 自動建立。
