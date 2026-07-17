# 資料庫 Schema v1（歷史草案）

> 注意：本文件是早期 7 表草案。現行可執行 schema 以 `alembic/versions/0001_baseline_schema.sql`、後續 Alembic migration、`docs/import_rules.md` 與實作程式為準。

第一版採用 7 張表，目標是讓 WIPS 全部欄位都有整理後的位置，同時保留 GPSS 等其他來源的擴充彈性。

## 資料表

| Table | 用途 |
|---|---|
| `source_files` | 匯入檔案紀錄，包含來源、檔名、hash、sheet、mapping 版本 |
| `raw_records` | 原始資料列，完整保存每列原始欄位到 `raw_data jsonb` |
| `patents` | 專利主表，一件專利一列 |
| `patent_sources` | 專利與原始資料列、來源檔案的關聯 |
| `patent_people` | 申請人、發明人、代理人、權利人等人名資料 |
| `patent_classifications` | IPC、CPC、FI、F-term、US Class 等分類碼 |
| `patent_attributes` | 其他所有欄位、稀有欄位、長文、連結與補充資料 |

## WIPS 欄位覆蓋規則

WIPS 欄位進資料庫時會走三層：

```text
核心欄位 -> patents / patent_people / patent_classifications
非核心欄位 -> patent_attributes
完整原始列 -> raw_records.raw_data
```

這代表 WIPS 148 欄不會遺失。常用欄位會進專門 table，偶爾才用的欄位會進 `patent_attributes`，原始值也會保留在 `raw_records.raw_data`。

## 日期與年份

專利主表同時保存完整日期與年份：

```text
application_date
application_year
publication_date
publication_year
```

完整日期用於追溯，年份用於統計、排序、圖表與報表。

## 去重規則

現行 WIPS 匯入不再把公告號/公開號混成 `publication_number`，也不再用多欄組合字串完全相等作為專利合併條件。

WIPS identifier lookup 使用：

```text
授權公告號 -> patents."授權公告號"
審查的公告號 -> patents."審查的公告號"
未審查的公開號 -> patents."未審查的公開號(轉換後)"
申請號 -> patents."申請號(轉換後)"
```

`patents."未審查的公開號"` 與 `patents."申請號"` 保存 WIPS 原值；緊鄰的
`(轉換後)` generated columns 是所有 dedupe、embedding、報表與前端下游使用值。
TW 四位西元年前綴減 1911，非 TW 的轉換後值等於原值。

查找優先序：

```text
授權公告號 -> 審查的公告號 -> 未審查的公開號 -> 申請號
```

`dedupe_key` 僅保存本列來源識別碼快照與 fallback row key，供來源追蹤與除錯使用，不作為合併唯一真相。

## 第一版暫不拆出的表

以下資料先放在 `patent_attributes`，等未來常用後再拆專門表：

```text
優先權
PCT
同族專利
引用 / 被引用
法律狀態
PDF / 圖片 / 外部連結
審判 / 訴訟
標準專利
長文欄位
```

## Python 環境

本專案使用 `uv` 管理 Python 環境與依賴。

```powershell
cd "D:\力山\專案\專利_ppt自動"
uv sync
```

WIPS 匯入 dry-run：

```powershell
uv run python -m backend.app.importers.wips_importer data\raw\wips_lishan_2026-07-01_001.xlsx --dry-run
```

正式匯入前需先建立 schema：

```powershell
& "D:\PostgreSQL\18\bin\psql.exe" -U postgres -d patent_ppt -f sql\001_schema.sql
```
