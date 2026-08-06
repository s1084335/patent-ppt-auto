## Context

現行 embeddings 經 PCA 後進 BERTopic/MiniBatchKMeans；incremental `partial_fit` 固定 k。既有 artifact、run chain、人工主題治理與兩通道 source registry 必須保持可追溯。

## Goals / Non-Goals

**Goals:** 新資料可依 cosine distance 形成新群；lambda 自動、可重現；舊 assignments 與治理功能不消失。

**Non-Goals:** 不改來源欄、不加任意 split、不要求使用者調 lambda。

## Decisions

1. **Cosine Online DP-Means。** 逐點指派最近中心；距離超過 lambda 建新中心。替代固定 k 的 `partial_fit`。
2. **保留 PCA 並重新 L2 normalize。** Cosine 距離的前提在降維後重新建立。
3. **Lambda 從 calibration distribution 推導。** 公式、樣本範圍與版本落 run metadata；不得只存在 code 常數。
4. **Artifact schema 顯式版本化。** 舊 artifact 不可誤當新模型讀取；選擇 fail loud 或一次性重建，不做模糊相容。
5. **新 topic 走既有 label job。** 只 enqueue 新 topic，避免重跑所有人工命名。

## 程式與測試落點

- `backend/app/clustering/model.py`、`runner.py`、`artifacts.py`
- `backend/app/clustering/sources.py`、`db_writer.py`
- `backend/app/worker/handlers.py` 與 topic-label follow-up
- Tests：模型等價類／邊界值、artifact version/hash、incremental run chain、新 topic label、merge/unmerge、cluster reports。

## 輸出契約

Run metadata 新增 algorithm/version、lambda/value source、PCA normalization 與新舊 topic 統計；artifact 保存 centers、counts 與可繼續增量的必要狀態。

## Risks / Trade-offs

- [順序敏感] → 固定 deterministic order 並測 permutation 影響界線。
- [lambda 過小產生碎群] → 以 calibration distribution 與最小群治理規則限制。
- [舊人工主題失效] → 先定 artifact migration 與 topic key 對映，再重跑正式資料。

## Migration Plan

先用純函式與合成向量 TDD，接著接入 artifact/run，最後在拋棄式 DB 與代表 workspace 驗證。正式 workspace 重跑前備份 topic state 與人工 labels。

