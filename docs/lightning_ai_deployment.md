# Lightning AI 部署準備

## 目標

- backend / worker 共用同一個 production image。
- 保留 GPU-capable `torch`，讓 embedding / clustering 可在 Lightning AI GPU 環境執行。
- PatentSBERTa 模型不包進 image，改由 GitHub Release 下載到 persistent volume。
- 資料庫只部署 PostgreSQL / pgvector 容器與 schema；本機資料值不搬上去。
- `data`、`output`、`model_artifacts` 使用 volume，不塞進 image。

## Image 瘦身原則

1. `Dockerfile` 使用 multi-stage build。
2. backend / worker 共用 image，用不同 command 啟動。
3. image 保留 GPU wheel，不改成 CPU-only `torch`。
4. `.dockerignore` 排除大型或本機資料：
   - `backend/models`
   - `data/model_artifacts`
   - `data/raw`
   - `output`
   - `backups`
   - `tests`

## PatentSBERTa 模型來源

Release:

```text
https://github.com/s1084335/patent-ppt-auto/releases/tag/patentsberta-v1
```

Asset:

```text
https://github.com/s1084335/patent-ppt-auto/releases/download/patentsberta-v1/PatentSBERTa.tar.gz
```

SHA256:

```text
20BA0214BF224FF9C579CC1AF268035400AF00456B1C0C13B8B9261260DA5940
```

部署前或啟動前執行：

```powershell
python scripts/ensure_model_artifact.py `
  --url "$env:PATENT_SBERTA_MODEL_URL" `
  --sha256 "$env:PATENT_SBERTA_MODEL_SHA256" `
  --target-dir "$env:PATENT_SBERTA_MODEL_DIR"
```

容器內模型目標位置：

```text
/app/data/model_artifacts/PatentSBERTa
```

必要環境變數：

```env
MODEL_ARTIFACT_ROOT=/app/data/model_artifacts
PATENT_SBERTA_MODEL_DIR=/app/data/model_artifacts/PatentSBERTa
PATENT_SBERTA_MODEL_URL=https://github.com/s1084335/patent-ppt-auto/releases/download/patentsberta-v1/PatentSBERTa.tar.gz
PATENT_SBERTA_MODEL_SHA256=20BA0214BF224FF9C579CC1AF268035400AF00456B1C0C13B8B9261260DA5940
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

## 資料庫部署邊界

Lightning AI 上部署 PostgreSQL / pgvector 容器與 schema，不搬本機資料值。

不做：

- 不 COPY `pgdata`。
- 不 COPY dump。
- 不匯入本機正式資料或測試資料。
- 不把資料庫資料塞進 backend image。

可做：

- 在空資料庫上套 migration 建 schema。
- 用部署環境自己的資料匯入流程建立資料。

## 啟動順序

1. 建立 persistent volume：
   - `/app/data`
   - `/app/output`
   - `/app/data/model_artifacts`
2. 啟動 PostgreSQL / pgvector。
3. 套用 migration。
4. 執行 `scripts/ensure_model_artifact.py`，確認 PatentSBERTa 已下載並通過 SHA256。
5. 啟動 backend。
6. 啟動 worker，並確認 GPU allocation。
7. smoke 驗收：
   - `/api/v1/ready`
   - 匯入 API
   - embedding job
   - clustering candidate job
   - report export job

## 驗收重點

- Docker build 不包含 `backend/models/PatentSBERTa/pytorch_model.bin`。
- 容器內 `PATENT_SBERTA_MODEL_DIR` 有完整模型檔後，embedding job 可載入模型。
- Lightning AI GPU worker 中 `torch.cuda.is_available()` 為 `True`。
- 空 DB 套 migration 後 backend ready。
- 不需要搬移本機 DB 資料值也能啟動服務。
