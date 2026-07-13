# 2026-07-07 交接：App Layer 第一版實作

本文件是給下一個執行 session（Opus）的完整實作指引。欄位設計已由使用者確認（輕量精簡版），照做即可，不需重新設計。

## 前提狀態（已完成，不要重做）

```text
資料庫 patent_ppt：raw_layer / core_layer / derived_layer 已有資料（407 筆專利）
app_layer schema 已存在（sql/006 建立），但沒有任何 table
report_engine / chart_runner 已可用（.venv\Scripts\python.exe 執行）
derived_layer.report_patent_base：407 筆、26 欄
uv 已安裝（C:\Users\user\.local\bin），plotly / pandas / kaleido 已入 pyproject
```

執行環境：

```powershell
cd D:\力山\專案\專利_ppt自動
# Python 一律用 .venv\Scripts\python.exe
# psql：D:\PostgreSQL\18\bin\psql.exe -U postgres -d patent_ppt
# PGPASSWORD 在 User 環境變數
```

## 設計背景（一段話）

`derived_layer.report_patent_base` 是可重算的乾淨母表，不記錄任務；`app_layer` 記錄「一次分析任務」的範圍快照、結果與匯出檔追溯，`analysis_id` 是全鏈追溯 key。核心原則：成功才寫入子表，失敗統一記在 `analysis_runs.error_message`；可推得的資訊不存；AI 文字只能落在 `analysis_outputs`。

---

## Step 1｜更新設計文件

檔案：`docs/app_layer_design.md`（修改）

- 三張表欄位改成下方 Step 2 的精簡版。
- `frontend_query_logs` 保留「暫不建立」段落。
- Rules 補兩條：

```text
analysis_outputs / export_runs 只寫成功結果；失敗原因統一記在 analysis_runs.error_message。
可從其他欄位推得的資訊不存（file_name、export_format 由 file_path 推得）。
```

## Step 2｜建表 SQL

檔案：`sql/012_app_layer_tables.sql`（新增）

先讀 `sql/009`–`011` 對齊既有慣例（id 型別、JSONB、時間戳寫法），再建三張表。欄位定案：

```text
app_layer.analysis_runs
  analysis_id              主鍵（依 009 慣例）
  analysis_name            TEXT NOT NULL
  analysis_type            TEXT NOT NULL      -- report / infringement / patentability
  status                   TEXT NOT NULL      -- pending / running / completed / failed（CHECK 約束）
  filter_json              JSONB
  parameters_json          JSONB              -- 含 ranking_limit / ipc_levels / family_dedup_mode
  selected_patent_ids_json JSONB              -- 選入 patent_id 陣列快照
  error_message            TEXT
  created_at               timestamptz NOT NULL DEFAULT now()
  completed_at             timestamptz

app_layer.analysis_outputs
  output_id       主鍵
  analysis_id     FK → analysis_runs，ON DELETE RESTRICT
  output_type     TEXT NOT NULL   -- chart_data / statistics / ai_summary / claim_comparison
  output_name     TEXT NOT NULL   -- 例 applicant_ranking
  result_json     JSONB NOT NULL
  ai_model        TEXT            -- 非 AI 為 NULL，格式 anthropic/claude-...
  prompt_version  TEXT            -- 非 AI 為 NULL
  created_at      timestamptz NOT NULL DEFAULT now()

app_layer.export_runs
  export_id        主鍵
  analysis_id      FK → analysis_runs，ON DELETE RESTRICT
  export_type      TEXT NOT NULL  -- report_html / chart_svg / excel / ppt
  file_path        TEXT NOT NULL
  file_hash        TEXT NOT NULL  -- sha256 hex
  parameters_json  JSONB
  created_at       timestamptz NOT NULL DEFAULT now()
```

- 索引：兩張子表的 `analysis_id` 各建一個。
- 不動 raw/core/derived 任何東西。

套用與驗證：

```powershell
& "D:\PostgreSQL\18\bin\psql.exe" -U postgres -d patent_ppt -f sql\012_app_layer_tables.sql
& "D:\PostgreSQL\18\bin\psql.exe" -U postgres -d patent_ppt -c "\dt app_layer.*"
```

驗收：三張表存在、欄位數 10 / 8 / 7。

## Step 3｜analysis runner CLI

檔案：`backend/app/app_layer/__init__.py`、`backend/app/app_layer/analysis_runner.py`（新增）

風格對齊 `report_engine.py`（argparse、`--filters-file` 支援 UTF-8 BOM、DB 連線走 `backend.app.db.connection`）。兩個子命令：

```text
create-analysis --name <名稱> [--type report] [--filters-file f.json] [--parameters-file p.json]
  1. 用 filter（沿用 report_engine 的 ALLOWED_FILTER_COLUMNS 白名單）查 derived_layer.report_patent_base
  2. 把命中的 patent_id 陣列寫入 selected_patent_ids_json
  3. 建 analysis_runs 一列，status='pending'
  4. stdout 輸出 JSON：{"analysis_id": N, "patent_count": M}

run-reports <analysis_id>
  1. status → running
  2. 對 7 支報表逐一執行，查詢範圍限制在該次快照：
     WHERE patent_id = ANY(selected_patent_ids)   ← 關鍵：查快照集合，不是全表
  3. 每支報表結果寫 analysis_outputs 一列
     （output_type='chart_data'，output_name=報表 key，result_json=rows）
  4. 全部成功 → status='completed'、completed_at=now()
     任一失敗 → status='failed'、error_message 記原因，已寫入的 outputs 保留
```

實作提示：報表查詢重用 `report_engine.run_report`，加一個可選參數（如 `patent_ids: list | None`）在 SQL 加 `patent_id = ANY(%s)` 條件。**改 `report_engine.py` 必須向下相容**（不帶參數行為完全不變）。

## Step 4｜chart_runner 掛上 export_runs

檔案：`backend/app/reports/chart_runner.py`（修改，向下相容）

加 `--analysis-id <N>`（可選）：

```text
未帶 --analysis-id：行為完全不變（trial 模式，目錄 report_trial_<ts>，不入庫）
帶 --analysis-id：
  1. 輸出目錄改 output/analysis_<id>_<ts>/
  2. 圖表數據改由該 analysis 的範圍產生（同 Step 3 的 patent_ids 限制）
  3. 每個產出檔（svg/html/json）算 sha256，逐檔寫 export_runs：
     export_type：index.html→report_html、*.svg→chart_svg、country_map.html→report_html
     parameters_json：ranking_limit / ipc_levels / cpc_levels
  4. stdout JSON 加 "analysis_id" 與 "export_count"
```

hash 用 `hashlib.sha256`，讀檔用 binary。

## Step 5｜端到端驗證（驗收標準）

```powershell
# 1. 建任務（無 filter＝全部 407 筆）
.venv\Scripts\python.exe -m backend.app.app_layer.analysis_runner create-analysis --name "app_layer 驗證"
# 預期：patent_count = 407

# 2. 跑報表入庫
.venv\Scripts\python.exe -m backend.app.app_layer.analysis_runner run-reports 1
# 預期：status=completed

# 3. 匯出並記錄
.venv\Scripts\python.exe -m backend.app.reports.chart_runner --analysis-id 1 --output-dir output

# 4. 查庫驗收
#    analysis_runs = 1 列 completed
#    analysis_outputs = 7 列
#    export_runs 列數 = 實際產出檔數
# 5. 重算其中一個檔的 sha256，與 export_runs.file_hash 一致
# 6. 回歸：不帶 --analysis-id 跑一次 chart_runner，行為與現在相同
```

負向測試：`run-reports 999`（不存在的 id）要乾淨報錯，不留半套資料。

## Step 6｜收尾

```text
1. py_compile 所有新增/修改的 .py
2. docs/report_field_matrix.md 僅在行為有變動時更新
3. 工作紀錄：.agents/work-logs/專利_ppt自動/<當天日期>.md 追加本次內容
4. 完成回報：修改摘要 / 影響範圍 / 驗證方式 / 下一步
```

## 紅線（不可違反）

```text
不動 raw_layer / core_layer / derived_layer 的表結構與資料
不清空、不重匯資料庫
chart_runner 與 report_engine 的既有用法必須向下相容
AI 相關欄位（ai_model / prompt_version）本階段只建欄位，不接 AI
不引入 Alembic（仍用 sql/ 檔管理，Docker 化時再上）
```

## 完成後的下一步（不在本次範圍）

```text
侵權比對：在同一框架掛 analysis_type='infringement'、output_type='claim_comparison'
Excel / PPT Exporter：產檔後同樣寫 export_runs
Docker Compose + Alembic migration 正式化
```
