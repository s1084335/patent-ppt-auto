# Database Operations

最後更新：2026-07-02

## 目的

本文件整理 PostgreSQL 初始化、重建、備份、還原與 Docker volume 相關風險。

目前原則：

```text
短期完整保留 raw_records + patent_attributes。
進 server 前先確保正確性與追溯。
資料穩定後 90 天再設計封存與清理策略。
現有 table 不新增或刪除。
```

## Init SQL 只會跑第一次

Docker PostgreSQL 的初始化 SQL 通常放在：

```text
/docker-entrypoint-initdb.d/
```

風險：

```text
只有全新 volume 第一次初始化時會執行。
volume 已存在後，修改 init SQL 不會自動套用。
```

因此不要只依賴 Docker init SQL 更新 schema。

目前專案正式重建 schema 檔：

```text
sql/005_six_table_schema.sql
```

用途：

```text
開發環境重建資料庫。
會清空舊資料。
正式執行前必須先備份。
```

## Docker Volume 風險

安全停止：

```powershell
docker compose down
```

高風險指令：

```powershell
docker compose down -v
docker volume rm <volume_name>
```

風險：

```text
會刪除 PostgreSQL volume。
資料庫資料會消失。
```

規則：

```text
任何包含 -v 的 Docker compose 指令都視為 destructive。
執行前必須確認已有 pg_dump 備份。
日常停止服務不要使用 docker compose down -v。
```

建議 Docker volume 使用明確名稱，避免誤刪匿名 volume：

```yaml
volumes:
  patent_pgdata:
```

## 備份

備份腳本：

```powershell
scripts/db_backup.ps1
```

輸出檔名：

```text
backups/patent_ppt_full_YYYYMMDD_HHMMSS.dump
backups/patent_ppt_schema_YYYYMMDD_HHMMSS.dump
```

執行：

```powershell
cd "D:\力山\專案\專利_ppt自動"
.\scripts\db_backup.ps1
```

測試只保留資料庫架構、欄位、索引與 constraint，不保留資料值：

```powershell
.\scripts\db_backup.ps1 -SchemaOnly
```

備份格式：

```text
pg_dump custom format (-F c)
```

備份模式：

```text
預設：full backup，保留 schema 與資料值，正式備份使用這個模式。
-SchemaOnly：schema-only backup，只保留架構，不保留資料值，供測試或架構快照使用。
```

建議規則：

```text
每次執行 sql/005_six_table_schema.sql 前，必須先備份。
每次正式大量匯入前，必須先備份。
正式備份必須使用預設 full backup。
測試架構快照可使用 -SchemaOnly。
至少保留最近 7 份正式備份。
```

## 還原

還原範例：

```powershell
& "D:\PostgreSQL\18\bin\pg_restore.exe" `
  -U postgres `
  -d patent_ppt `
  --clean `
  --if-exists `
  "backups\patent_ppt_YYYYMMDD_HHMMSS.dump"
```

注意：

```text
還原會改動現有資料庫。
還原前應先確認要還原的 dump 檔。
```

## 重建與匯入

重建加匯入腳本：

```powershell
scripts/db_reset_and_import.ps1
```

這是破壞性操作，會執行：

```text
1. 先備份目前資料庫。
2. 套用 sql/005_six_table_schema.sql。
3. 匯入指定 WIPS 檔案。
```

執行時必須明確加上：

```powershell
-ConfirmReset
```

範例：

```powershell
cd "D:\力山\專案\專利_ppt自動"
.\scripts\db_reset_and_import.ps1 `
  -InputFile "data\raw\wips_lishan_2026-07-01_001.xlsx" `
  -ConfirmReset
```

## 同 File Hash 重複檔案

目前匯入程式策略：

```text
同 source_system + file_hash 已存在時，預設跳過整個檔案。
不重複寫 source_files / raw_records / patents / patent_people / patent_attributes。
```

目的：

```text
保留追溯能力。
避免同一份 WIPS 檔案被重複匯入造成 raw_records 與 patent_attributes 膨脹。
```

## 90 天後封存策略

資料穩定後再評估：

```text
raw_records 壓縮或封存。
舊備份清理。
import_runs 匯入狀態表。
差異紀錄表。
```

目前不新增上述 table。
