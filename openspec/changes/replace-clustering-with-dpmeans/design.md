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

## 實作期補充（2026-08-09，實作中確認）

**DP-Means 的主題自行抽取關鍵詞（class-TF-IDF）。**

> ⚠ **2026-08-09 同日修正**：本節原本寫「DP-Means 不產關鍵詞，coherence／
> diversity 只能填 None」。使用者指出「主題一致性與主題多樣性可以繼續用」，
> 查證後確認**原判斷錯誤**——`topic_cv_coherence_per_topic` 與 `topic_diversity`
> 都只需要 `top_terms`，不綁 BERTopic（gensim 算 c_v 用的是文件本身）。缺的
> 只是「產出關鍵詞」這一步，補上即可，兩個既有品質指標完全適用。

`clustering/keywords.py` 以 class-TF-IDF 抽取每群關鍵詞：把每群文件併成一個
「類別文件」算詞頻，再依該詞出現在幾個類別中折減。與 BERTopic 的 c-TF-IDF
同原理，只用既有 tokenizer，不引入新依賴。

| 消費點 | 影響 | 處置 |
|---|---|---|
| `ai_topic_label_runner`（AI 命名） | **無影響** | 該模組有紅線黑名單禁止 keywords 進 CLI payload——給了關鍵字，LLM 會覆述關鍵詞而非讀專利內容命名。命名靠代表文檔 |
| `api/topics.py` → 前端主題卡 | 照常有關鍵詞 | 無需改動 |
| 校準候選的品質指標 | coherence／diversity 照常計算 | 無需改動 |

**代表文檔改用「離中心最近的 N 篇」**（`engine.plan_finalize_topics`）。向量直接
算得出來，不需 c-TF-IDF，語意上就是「最能代表這群的文件」——AI 命名讀的正是這些。

**K 選擇路徑改為隔離而非移除**（tasks 2.5 已回寫）：使用者定案「確定新引擎穩定後
舊引擎才會移除」，驗收期間舊引擎必須能跑，移除會讓 rollback 失效。

## Risks / Trade-offs

- [順序敏感] → 固定 deterministic order 並測 permutation 影響界線。
- [lambda 過小產生碎群] → 以 calibration distribution 與最小群治理規則限制。
- [舊人工主題失效] → 先定 artifact migration 與 topic key 對映，再重跑正式資料。
- [DP-Means 主題無關鍵詞] → 命名不受影響（見上表）；前端顯示待 3.3 實測確認。

## Migration Plan

先用純函式與合成向量 TDD，接著接入 artifact/run，最後在拋棄式 DB 與代表 workspace 驗證。正式 workspace 重跑前備份 topic state 與人工 labels。

