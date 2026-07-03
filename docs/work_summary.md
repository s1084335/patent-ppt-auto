# 工作摘要

最後更新：2026-07-02

## 專案目標

建立一套專利資料工具，第一階段先完成資料庫層：

```text
WIPS / GPSS 原始資料
-> Python 匯入與欄位整理
-> PostgreSQL
-> 後續 FastAPI 查詢層
-> 前端 / Obsidian / 報表工具使用
```

正式專案目錄：

```text
D:\力山\專案\專利_ppt自動
```

## 目前技術棧

```text
PostgreSQL 18.4
Python
uv
DBeaver
```

PostgreSQL 安裝位置：

```text
D:\PostgreSQL\18
```

資料庫：

```text
patent_ppt
```

## 已完成

已建立資料庫 schema：

```text
sql\001_schema.sql
sql\002_fix_attribute_value_index.sql
sql\003_simplify_source_tracking.sql
sql\004_separate_patent_system_fields.sql
```

已精簡為 6 張資料表與 1 個 view：

```text
source_files
raw_records
patents
patent_sources
patent_people
patent_attributes
patent_source_summary
```

已建立 WIPS 匯入模組：

```text
backend\app\importers\wips_importer.py
backend\app\mappings\wips.py
backend\app\db\connection.py
backend\app\transforms\*.py
```

已完成 WIPS dry-run：

```text
file: data\raw\wips_lishan_2026-07-01_001.xlsx
sheet: download
headers: 148
records: 407
normalized_records: 407
```

## 最新匯入規則

正式規則檔：

```text
docs\import_rules.md
```

核心規則：

```text
1. 匯入時主要只做分 table。
2. 欄位內容不混合、不改名、不跨欄位擇一覆蓋。
3. 多人/多公司不拆。
4. 分類碼不拆。
5. 授权公告号、未审查的公开号、申请号 放在 patents，資料庫欄位顯示為繁體，不混成 publication_number 或 application_number。
6. 公告日/公開日欄位原樣保留，不混成 publication_date。
7. 去重使用 授权公告号、未审查的公开号、申请号 三欄組合。
8. 三欄都空白時，用 source_file_id + row_number 作為獨立列。
9. dedupe_key 只用於去重，不代表資料欄位合併。
10. 整理後欄位層不能依賴 raw_records 補欄位存在性。
11. patents 每筆 Excel 資料一列，只放專利本身欄位、year 欄位，以及去重用的三個號碼欄位。
12. patent_people 每筆 Excel 資料寫入一列，人員來源欄位各自成欄，空欄位保留為 NULL。
13. 非 patents / 非 patent_people 的 Excel 欄位都要寫入 patent_attributes 寬表，空值也要保留為 NULL。
14. WIPS 欄位名稱支援常見繁體正規化到既有 mapping，但 raw_records 保留原始欄名。
15. 同一 dedupe_key 已存在時，patents / patent_people 依來源優先權更新，策略為 incoming_source_priority。
16. 既有值與新來源值不同時，以新來源值更新主資料，來源差異由 raw_records / patent_sources / patent_attributes 保留追溯。
17. 不同來源原始資料保留在 raw_records / patent_sources / patent_attributes 追溯。
18. WIPS 來源格式支援 XLSX / CSV / TXT / XML / MDB，格式只影響讀取層，不改分表與去重規則。
19. MDB 匯入需要 pyodbc 與 Microsoft Access ODBC Driver。
```

## 資料表定位

```text
source_files              匯入檔案紀錄
raw_records               原始列完整 JSON
patents                   專利本身欄位，year 欄位可放在此表
patent_sources            patents 與 raw_records / source_files 對應，保存 dedupe_key
patent_people             人員來源欄位，欄位值原樣不拆、不合併
patent_attributes         其他 WIPS 原欄位寬表，包含分類欄位、圖檔、連結、權利、行政、引用等欄位
patent_source_summary     view，即時計算來源檔案與匯入時間
```

## 目前資料庫狀態

目前注意：

```text
已執行 sql\005_six_table_schema.sql。
目前是 6 張實體表 + 1 個 view。
目前 source_files / raw_records / patents / patent_sources / patent_people / patent_attributes 皆為 0 筆。
Codex session 讀不到 PGPASSWORD。
正式重匯需要由使用者 PowerShell 執行。
```

最新 schema 已精簡匯入追蹤：

```text
source_files 每次匯入都新增一筆。
file_hash 不再 UNIQUE。
source_files 只保留來源檔案、hash、筆數、匯入時間。
patents 只放專利本身欄位，year 欄位可放在 patents。
dedupe_key 移到 patent_sources。
source_summary 不再實體存進 patents，改由 patent_source_summary view 即時計算。
分類欄位不另開表，直接進 patent_attributes 對應欄位。
created_at / updated_at 不混入專利內容表。
舊資料需先執行 sql\005_six_table_schema.sql 清空後重匯。
```

## 下一步

使用者在 PowerShell 執行重匯：

```powershell
cd "D:\力山\專案\專利_ppt自動"
$env:UV_CACHE_DIR = ".uv-cache"
$env:PGPASSWORD = [Environment]::GetEnvironmentVariable("PGPASSWORD", "User")

& "D:\PostgreSQL\18\bin\psql.exe" -U postgres -d patent_ppt -f sql\005_six_table_schema.sql

python -m uv run python -m backend.app.importers.wips_importer data\raw\wips_lishan_2026-07-01_001.xlsx
```

查資料表筆數：

```powershell
& "D:\PostgreSQL\18\bin\psql.exe" -U postgres -d patent_ppt -c "SELECT 'source_files' AS table_name, count(*) FROM source_files UNION ALL SELECT 'raw_records', count(*) FROM raw_records UNION ALL SELECT 'patents', count(*) FROM patents UNION ALL SELECT 'patent_sources', count(*) FROM patent_sources UNION ALL SELECT 'patent_people', count(*) FROM patent_people UNION ALL SELECT 'patent_attributes', count(*) FROM patent_attributes;"
```

重匯後抽查：

```text
1. raw_records 是否 407 筆。
2. patent_attributes 是否以寬表保留其他 WIPS 原欄位。
3. patent_people 是否每件專利一列，且多人/多公司同格保存在對應來源欄位。
4. patent_attributes 是否保留分類碼欄位，例如 Orig. CPC(Main)、Curr. IPC(All)。
5. patents 是否透過 patent_sources.dedupe_key 依 授权公告号、未审查的公开号、申请号 三欄組合去重。
```

## 重要提醒

欄位保存與去重是兩件事：

```text
欄位保存：原本是什麼欄位，就按 mapping 放到對應資料表欄位；資料庫實體欄位使用繁體中文或英文。
去重：使用 授权公告号、未审查的公开号、申请号 三欄組合。
```
