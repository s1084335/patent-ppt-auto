# Alembic Migrations

正式 schema 管理機制。連線設定由 `alembic/env.py` 依 `DATABASE_URL` / `PG*` 環境變數組出
（與 `backend/app/db/connection.py` 同源），強制使用 psycopg v3 driver，`alembic_version`
建在 `public` schema。

## 執行（本機用 .venv）

```powershell
cd D:\力山\專案\專利_ppt自動
$env:PGPASSWORD = [Environment]::GetEnvironmentVariable("PGPASSWORD", "User")

# 全新 DB：建出完整四層 schema
.venv\Scripts\python.exe -m alembic upgrade head

# 既有 DB（已有 schema）：只標記版本，不重跑 DDL、不動資料
.venv\Scripts\python.exe -m alembic stamp head

# 查目前版本
.venv\Scripts\python.exe -m alembic current
```

指向不同資料庫時覆寫 `PGDATABASE`（或設 `DATABASE_URL`）。

## Baseline

- `versions/0001_baseline_schema.py` + `0001_baseline_schema.sql`。
- baseline 由開發 DB 的最終狀態（`sql/001-012` 套用後）以 `pg_dump --schema-only --no-owner
  --no-privileges` 匯出、清掉 psql meta 指令而成，涵蓋 raw / core / derived / app 四層與
  `core_layer.patent_source_summary` view。
- `upgrade` 讀該 `.sql` 一次執行；`downgrade` 以 CASCADE drop 四個 schema。

## 後續改 schema 的流程

```text
1. 改 DB 結構一律新增 alembic revision：
   .venv\Scripts\python.exe -m alembic revision -m "描述"
2. 在新 revision 的 upgrade()/downgrade() 寫 DDL（op.execute 或 op.* API）。
3. 本機 alembic upgrade head 驗證。
4. 不再新增 sql/ 檔；sql/001-012 僅保留為歷史紀錄。
```

## 容器（Docker 階段）

migrate container 使用 backend image，command 為 `alembic upgrade head`，跑完即停；
app-runner / worker 不執行 migration。
