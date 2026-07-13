# Docker 工作流（第一階段：資料庫容器）

目前只有 postgres 容器；backend 尚未設計。本機工具與未來後端透過 **localhost:5433** 連容器 DB（本機原生 PostgreSQL 18 佔 5432，兩者互不影響）。

## 檔案

```text
docker-compose.yml   postgres 服務 + migrate/backend/frontend 註解占位
.env.example         範本（.env 不進版控）
alembic/             schema 管理（屬於未來 backend image，過渡期由本機執行）
```

## 啟動

```powershell
cd D:\力山\專案\專利_ppt自動
# 首次：Copy-Item .env.example .env 並填 POSTGRES_PASSWORD
docker compose up -d postgres
docker inspect --format '{{.State.Health.Status}}' patent-postgres   # healthy 才算好
```

## 灌 / 升級 schema（過渡做法：本機 alembic → 容器）

```powershell
$env:PGHOST="localhost"; $env:PGPORT="5433"; $env:PGDATABASE="patent_ppt"
$env:PGUSER="postgres";  $env:PGPASSWORD="<.env 裡的 POSTGRES_PASSWORD>"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current    # 應為 0001_baseline_schema (head)
```

backend image 建好後，此步改由 migrate 容器執行（同一份 alembic/，指令不變）。

## 連線資訊（給後端 / 工具）

```text
host: localhost（容器間則用服務名 postgres）
port: 5433（容器間 5432）
db  : patent_ppt
user: postgres
密碼: .env 的 POSTGRES_PASSWORD
```

DBeaver：新增連線 → PostgreSQL → localhost:5433 / patent_ppt。

## 容器內查驗

```powershell
docker exec patent-postgres psql -U postgres -d patent_ppt -c "\dt raw_layer.*"
docker exec patent-postgres psql -U postgres -d patent_ppt -c "SELECT version_num FROM alembic_version;"
```

## 資料持久化

- named volume `ppt_pgdata`（Docker 管理，不綁本機路徑）。
- `docker compose down` 停容器**保留**資料；`docker compose down -v` 才會**刪除** volume。

## 已驗證狀態（2026-07-07）

```text
postgres:18（PG 18.4, UTF8）healthy
alembic upgrade head 成功：raw2/core4/derived3/app3 共 12 表 + view，247 欄
中文欄名（授權公告號等）正常
alembic_version = 0001_baseline_schema
```

## 下一階段（backend 設計完成後）

```text
1. 撰寫 backend Dockerfile（python + uv + backend/ + alembic/）
2. compose 打開 migrate 服務：command: alembic upgrade head，跑完即停
3. compose 打開 backend 服務：掛載 ./data ./output，跑匯入/報表/分析 CLI
4. 容器內全流程驗證：importer 407 筆 → derived refresh → analysis → chart → export_runs
```
