# 資料庫 Schema v1

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

第一版固定使用：

```text
dedupe_key = publication_number + "|" + application_number
```

WIPS 的 `publication_number` 來源優先序：

```text
授權公告號 -> 未審查的公开号 -> 审查的公告号
```

GPSS 則預計使用：

```text
PN -> publication_number
AN -> application_number
```

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
