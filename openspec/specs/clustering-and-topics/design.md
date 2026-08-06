# Clustering and Topics Design

## 架構與資料流

每個 workspace、source field 形成獨立 run chain。文本由 `sources.py` 的 registry 指定，經前處理、PatentSBERTa embedding、PCA 與現行 BERTopic/MiniBatchKMeans 產候選；finalize 才把主題與 assignments 落庫。模型 artifact 存檔，DB 保存 key/hash 與 run metadata。

主題治理由 `TopicRepository` protocol 定義，PostgreSQL adapter 是唯一正式實作。merge/unmerge 經工作佇列執行，rename 可直接更新正式狀態。排除資料另存 review 與 restore snapshot。

## 程式落點

- 通道 registry：`backend/app/clustering/sources.py`
- 前處理與模型：`backend/app/clustering/preprocessing.py`、`model.py`
- 執行：`backend/app/clustering/runner.py`、`db_writer.py`
- Artifact：`backend/app/clustering/artifacts.py`
- Workspace／排除：`workspace_service.py`、`exclusions.py`
- Topic API／repository：`backend/app/api/clustering.py`、`backend/app/api/topics.py`、`backend/app/repositories/`

## 測試證據

- `tests/test_clustering_model.py`
- `tests/test_clustering_runner.py`
- `tests/test_clustering_artifacts.py`
- `tests/test_clustering_artifact_hash_contract.py`
- `tests/test_clustering_0021_persistence.py`
- `tests/test_api_clustering*.py`
- `tests/test_api_topics*.py`
- `tests/test_postgres_topic_repository.py`
- `tests/test_merge_after_incremental.py`
- `tests/test_merge_history_status.py`
- `tests/test_assigned_patents_across_runs.py`
- `tests/test_exclusion_*.py`

## 輸出契約

輸出包括 calibration candidates、metrics、candidate explanation、final topics、assignments、run metadata、artifact key/hash、merge history、exclusion reviews 與 per-channel AI labels。報表只消費正式 finalized topic state。

## 已知未完成

現行增量 MiniBatchKMeans 的 k 固定，不能自然長出新主題；DP-Means 替換方案必須在 active change 驗收後才可改寫本 baseline。

