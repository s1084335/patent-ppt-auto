# Docker 工作流（現況與固定五容器目標）

目前已進入 backend／worker 整合階段：基礎 Compose 包含 postgres、backend、worker；前端完成後再加入 nginx、frontend，最終固定五個常駐 service，不增加 Redis／Celery。本機預設透過 `127.0.0.1:5433` 連 DB、`127.0.0.1:8000` 連 backend；伺服器可由環境變數改綁定位址、port、image registry 與資料路徑。

## 檔案

```text
Dockerfile           backend／worker 共用 image，包含本機 PatentSBERTa
docker-compose.yml   postgres／backend／worker 基礎服務
docker-compose.gpu.yml  worker 的可選 GPU overlay
.dockerignore        排除 secrets、資料、輸出與開發快取
.env.example         範本（.env 不進版控）
alembic/             schema 管理，透過 backend image 一次性執行
```

## 啟動

```powershell
cd D:\力山\專案\專利_ppt自動
# 首次：Copy-Item .env.example .env 並填正式密碼與部署路徑
docker compose config
docker compose up -d --build postgres backend worker
docker compose ps

# NVIDIA runtime 可用時，改用 GPU overlay
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

## 灌／升級 schema（一次性容器，不隨服務啟動）

```powershell
docker compose run --rm backend alembic current
docker compose run --rm backend alembic upgrade head
```

目前 DB 已在 migration head；一般啟動不得自動執行 upgrade。只有正式部署包含新 migration 時才先執行一次性命令，失敗就停止更新，不啟動新版 backend／worker。

## 連線資訊（給後端 / 工具）

```text
host: 127.0.0.1（容器間固定用 Compose service 名 `postgres`）
port: 5433（容器間 5432）
db  : patent_ppt
user: postgres
密碼: .env 的 POSTGRES_PASSWORD
```

DBeaver：新增連線 → PostgreSQL → localhost:5433 / patent_ppt。

## 容器內查驗

```powershell
docker compose exec postgres psql -U postgres -d patent_ppt -c "\dt raw_layer.*"
docker compose exec postgres psql -U postgres -d patent_ppt -c "SELECT version_num FROM alembic_version;"
```

## 資料持久化

- named volume `ppt_pgdata`（Docker 管理，不綁本機路徑）。
- 分群模型檔存放於 `${DATA_HOST_PATH}/model_artifacts`，並掛載到容器的 `MODEL_ARTIFACT_ROOT`。
- `derived_layer.topic_runs.model_artifact_path` 只存 `clustering/workspace_.../run_....pkl` 相對 key；實際位置由執行環境的 `MODEL_ARTIFACT_ROOT` 決定。
- 本機 Python 預設使用專案 `data/model_artifacts`；正式伺服器可用 `DATA_HOST_PATH` 與 `CONTAINER_MODEL_ARTIFACT_ROOT` 調整掛載位置，不必修改 DB。
- `docker compose down` 停容器**保留**資料；`docker compose down -v` 才會**刪除** volume。

既有 DB 若保存 Windows 或 `/app/data/model_artifacts/...` 絕對路徑，讀取時會將 `model_artifacts` 後方的部分映射到目前 root；新 run 一律只寫相對 key。備份與還原時必須同時保存 PostgreSQL 與 `${DATA_HOST_PATH}/model_artifacts`。

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
