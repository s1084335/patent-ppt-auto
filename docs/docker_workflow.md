# Docker 工作流（現況與固定五容器目標）

目前實作只有 postgres 容器；最終 Docker Compose 固定為 nginx、frontend、backend、worker、postgres 五個 service／container，不再增加其他常駐容器。本機工具目前透過 **localhost:5433** 連容器 DB（本機原生 PostgreSQL 18 佔 5432，兩者互不影響）。

## 檔案

```text
docker-compose.yml   現有 postgres；目標固定 nginx/frontend/backend/worker/postgres
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

backend image 建好後，改用 `docker compose run --rm backend alembic upgrade head`。migration 不建立 Compose service，完成後不留下第六個容器。

資料庫更新／部署流程可自動執行此命令，但每次都是新建一次性 container，並非常駐服務或 `restart: always`。migration 失敗時必須停止更新，不啟動新版 backend／worker。

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

## 已驗證狀態（2026-07-15）

```text
pgvector/pgvector:0.8.5-pg18-trixie（PG 18.4, pgvector 0.8.5, UTF8）healthy
alembic upgrade head 成功：raw2/core5/derived7/app5，共 19 張 table + core1 view
中文欄名（授權公告號等）正常
alembic_version = 0007_reorder_embeddings
目前兩個 WIPS 來源檔共 932 筆專利，file-hash 冪等、VECTOR(768) 與完整分類 FK 測試通過
PatentSBERTa 已寫入 917 筆 wips_independent_claims 向量，全部 768 維
原 511 筆向量內容指紋維持 057d6ce7df52221a91c3986414a90a5b
embedding 追蹤專利號保留四號碼優先序選中欄位的來源格式，例如 12667896，不主動組合 country code
topic model derived tables 已精簡為 topic_runs / topics / topic_assignments / topic_candidates
patent_embeddings 已精簡為 14 欄，migration 前後向量內容指紋一致
```

正式產生或補齊 DB embedding：

```powershell
$env:PGHOST="localhost"; $env:PGPORT="5433"; $env:PGDATABASE="patent_ppt"
$env:PGUSER="postgres"; $env:PGPASSWORD="<.env 裡的 POSTGRES_PASSWORD>"
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"
uv run python -m backend.app.clustering.db_writer `
  --model-path backend/models/PatentSBERTa --device cuda --batch-size 8
```

writer 只讀取 `core_layer.patents."獨立項[KR,JP,US,CN,EP,IN]"`，向量只寫入
`core_layer.patent_embeddings`。同一 patent、來源欄位、模型權重、前處理版本及文本 hash 已存在時會直接重用。

## 固定五容器目標

```text
1. nginx：唯一對外入口；/ 轉 frontend，/api/ 轉 backend
2. frontend：HTML / JS / CSS，不直接連 PostgreSQL
3. backend：API、驗證、Workspace、Job 建立與查詢，不執行長時間任務
4. worker：統一執行分群、報表、Embedding、案件比對，不再拆多個 worker
5. postgres：正式資料庫，並保存 Job queue、狀態與結果
```

第一版不新增 Redis；Backend 建立 Job 後寫入 PostgreSQL，Worker claim Job 並回寫狀態與結果。migration 使用 backend image 的一次性 `--rm` 命令，不是第六個 service。驗收時 `docker compose ps -a` 穩態只能看見上述五個容器。

## 下一階段實作順序

```text
1. 撰寫共用 backend image（backend API、worker runtime、alembic）
2. 補 nginx 與 frontend image / config
3. 建立 PostgreSQL Workspace / Job queue / 狀態資料模型
4. 建立 backend API 與 worker claim / retry / result 流程
5. 將分群、報表、Embedding、案件比對接入同一 worker
6. schema 有變更時，由更新／部署流程自動啟動一次性 migration；平常直接啟動五個 service
7. 驗證入口、Job 流程與穩態容器數量
```
