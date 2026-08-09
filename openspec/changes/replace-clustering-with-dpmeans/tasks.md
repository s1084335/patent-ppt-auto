## 1. 基準與演算法契約

- [x] 1.1 固定技術/功效雙通道樣本、embedding/model 版本與現行 MiniBatchKMeans 輸出作為比較基準　（基準：run1 技術 5 主題/44 件、run2 功效 8 主題/44 件；模型設定見 ModelConfig）
- [x] 1.2 定義 cosine Online DP-Means 的向量正規化、PCA、lambda 推導、樣本順序、seed、outlier 與空/小樣本行為　（契約寫成可執行版：tests/test_dpmeans_core.py）
- [x] 1.3 定義 artifact schema/version、cluster run metadata 與舊 run 的讀取相容策略　（schema v2：`algorithm` + `dpmeans_state`；舊 pickle 缺欄補預設 `minibatch_kmeans`，見 tests/test_dpmeans_artifact.py）

## 2. TDD 實作

- [x] 2.1 Red：新增距離、建群門檻、中心更新、決定性、雙通道與資料驅動 lambda 的單元測試，保存失敗原因
- [x] 2.2 Green：以最小 Online DP-Means 核心實作通過演算法測試，不先改 UI 或移除現行引擎
- [x] 2.3 Red：新增 calibrate/finalize/incremental、artifact round-trip 與新舊版本讀取整合測試
- [x] 2.4 Green：接上 clustering job、repository、topic label 與 API，確保技術/功效結果分離且可追溯

  接線落點與測試：
  - **引擎分流**（`clustering/engine.py`）：finalize 看 `CLUSTERING_ALGORITHM` flag；
    增量跟隨 artifact 記錄的演算法，**不看 flag**（中途換引擎會讓中心格式對不上且不報錯）。
  - **finalize**（`runner._finalize_with_dpmeans`）：lambda 由資料推導、artifact 存
    `dpmeans_state`、run metadata 記 algorithm 與 lambda 推導方法。落庫尾段抽成
    `_finish_final_topics`，兩條路徑共用。
  - **增量**（`workspace_service.incremental_workspace`）：新主題與「未知舊 ID」分開處理
    ——⚠ 原 fallback 會把 DP-Means 新主題靜默併進舊主題，跑完沒有錯誤、新主題一個都沒有。
  - **topic label**（`handlers._enqueue_topic_label_for_new_topics`）：只在**真的長出
    新主題**時排，否則每次增量都重跑整個 workspace 的 AI 命名（max_attempts=1，重跑＝真的再花額度）。
  - **API**：無需改動。DP-Means 主題走既有 topic_state_json 通道；keywords 為空清單，
    既有契約測試（`test_api_topics_keywords_contract`）通過。
  - 測試 44 支：核心 27、artifact 11（既有）＋ finalize 11、分流 11、新主題落地 16、命名接續 5。
- [x] 2.5 Refactor：全綠後抽離共用向量前處理並**隔離**已無用途的 K 選擇路徑；保留必要 rollback feature flag

  - **共用向量前處理：不另加抽象**。查證後 L2 normalize 只有 `dpmeans.l2_normalize`
    一個定義處，calibrate／finalize／incremental 三條路徑都經它；PCA 沿用既有
    `fit_incremental_pca`／`reduce_with_incremental_pca`。目標（前處理不得有第二份）
    已達成，再包一層只會是轉手的碎塊——依 AGENTS.md「新增抽象前先確認它降低實際
    複雜度」不加。
  - **隔離 K 選擇路徑**：`_calibrate_with_dpmeans` 跑一次、產一個候選，不掃七組 k。
    ⚠ 不隔離的後果不只是浪費算力：使用者會被要求在三個**完全不影響結果**的候選之間
    選一個，選了也沒反應——這種「操作沒有效果」比報錯更難察覺。
  - **品質指標回 None 不回 0**：coherence／diversity／balance 都算在 c-TF-IDF top terms
    上，DP-Means 沒有。填 0 會讓前端顯示「品質 0 分」，那是憑空捏造的壞消息。
  - **rollback flag**：`CLUSTERING_ALGORITHM` 未設＝舊引擎（`test_default_is_kmeans`）；
    增量跟隨 artifact 不看 flag（`test_incremental_ignores_feature_flag`），所以切回舊
    引擎不會讓既有 DP-Means workspace 讀錯格式。
  - **落庫尾段抽離**：`_finish_final_topics` 供兩條路徑共用（見 2.4）。

  > ⚠ **2026-08-09 修正（規格被現實推翻，回寫理由）**：本項原文為「**移除**已無用途的
  > K 選擇路徑」。使用者當日定案為「feature flag 並存，**確定新引擎穩定後舊引擎才會
  > 移除**」——舊引擎在驗收期間仍必須能跑，K 選擇路徑是它的必要組成，此時移除會讓
  > rollback 失效。故本輪改為**隔離**（讓 DP-Means 不經過該路徑），實際移除留待使用者
  > 確認新引擎穩定後另開 change。

## 3. 驗證與輸出

- [ ] 3.1 比較基準與 DP-Means 的群數、singleton 比例、穩定度、人工可讀性與執行時間，不以單一分數宣稱成功
- [ ] 3.2 執行 clustering/topic/API 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.3 產出 cluster artifact、run metadata、技術/功效 topic labels 與代表性 UI/API 結果，確認重跑可再現
- [ ] 3.4 記錄未測規模與效能風險，經使用者確認群組品質與回復方案後才 archive
