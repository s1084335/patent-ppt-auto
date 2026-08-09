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

- [x] 3.1 比較基準與 DP-Means 的群數、singleton 比例、穩定度、人工可讀性與執行時間，不以單一分數宣稱成功

  ### 實驗設計（2026-08-09 使用者定案）

  ⚠ **調參的方向是「掃 lambda、用指標挑區間」**，不是「找一個公式剛好給出想要的
  群數」——後者是倒果為因，換批資料就不成立。我第一版就是這樣做的（看到 P25 給
  7 群才選 P25），使用者當場指出。

  四項判準（及格制，任一不過即淘汰）＋ 四指標加權排序（沿用 `model.RANKING_WEIGHTS`，
  不複製第二份）。判準與門檻見 `scripts/compare_clustering_engines.py` 檔頭。

  ### 兩次被實測推翻的公式

  | 版本 | 公式 | 技術（n=35） | 功效（n=44） | 結果 |
  |---|---|---|---|---|
  | v1 | 最近鄰距離 P75 | 18 群、9 單點 | 25 群、15 單點 | ⚠ 碎裂。根因是**衡量的量選錯**——最近鄰回答「最近的鄰居有多近」，建群門檻要回答的是「一個群的半徑該多大」 |
  | v2 | 全體距離 P25／P33 | 數字對得上 | 數字對得上 | ⚠ 倒果為因，且固定分位等於假設所有批次適用同一半徑 |
  | **現行** | **每批自動掃描選擇** | λ=0.906、7 群 | λ=0.957、5 群 | 兩通道各自選出不同 λ——這才是「各自適合」 |

  ### 最終結果（workspace 3）

  | 通道 | n | λ | 群數 | 各群件數 | coherence | diversity | balance | 加權分 |
  |---|---|---|---|---|---|---|---|---|
  | 技術（獨立項） | 35 | 0.906 | 7 | 10,7,7,5,4,1,1 | 0.805 | 0.829 | 0.889 | 0.721 |
  | 功效（效果摘要） | 44 | 0.957 | 5 | 13,13,9,6,3 | 0.577 | 0.620 | 0.932 | 0.550 |

  對照 1.1 基準（MiniBatchKMeans 技術 5／功效 8）。執行時間：`select_lambda`
  在 n=44 約 6 秒（掃 18 點含穩定度與 coherence）。掃描原始資料留在
  `output/_verify/dpmeans/`。
- [ ] 3.2 執行 clustering/topic/API 目標測試、相關模組回歸與 `scripts/verify_module.py`

  | 項目 | 結果 |
  |---|---|
  | 目標測試（9 支 DP-Means 測試檔） | **68 過**，0 紅 |
  | 範圍回歸（`-k dpmeans/topic/clustering/handler/artifact/keyword/candidate/ranking`） | **614 過、1 紅** ——`test_default_report_names_match_definitions`，⚠ **既有失敗**（`DEFAULT_REPORT_NAMES` 12→13，PR #19 合併時即存在），非本輪造成 |
  | `verify_module.py` 靜態分析 | 修正中（首輪 5 個新增行問題，門檻 0） |
  | `verify_module.py` 圈複雜度 | 修正中（首輪 7 支超 B） |
  | `verify_module.py` 覆蓋率 | 首輪 85%（門檻 90%）——未覆蓋集中在 `runner`／`workspace_service` 的接線行，需真實 DB，由 3.3 端到端涵蓋 |
- [ ] 3.3 產出 cluster artifact、run metadata、技術/功效 topic labels 與代表性 UI/API 結果，確認重跑可再現
- [ ] 3.4 記錄未測規模與效能風險，經使用者確認群組品質與回復方案後才 archive

  ### 未測與風險（2026-08-09 如實記錄）

  | # | 風險 | 現況 | 影響 |
  |---|---|---|---|
  | R1 | **真實資料的通用性樣本不足** | 只驗過 workspace 3 的兩個通道（35／44 件，滑雪機領域）。**合成資料**已補測（見 R2 表）：8 個真實群在 n=100／300／600／1000 全部正確選出 8 群 | ⚠ 合成資料的群是乾淨分離的，真實專利文本沒那麼理想。**不同技術領域仍未測**——判準門檻是在滑雪機這批上定的 |
  | R2 | **大規模效能**（已量測） | n=100→10.4s、300→13.8s、600→24.5s、1000→46.1s（皆正確選出 8 群） | n≤1000 可接受（校準本就是耗時流程，舊引擎掃 7 組 k 每組跑一次 BERTopic 更慢）。⚠ **n≥5000 未測**，外推約 4 分鐘以上；屆時需要縮減掃描點數或改用向量化實作 |
  | R3 | **順序敏感的長期效應** | 判準④只保證「換順序群數變動 ≤1」 | DP-Means 先看到的點決定中心起點。⚠ 新增專利後重跑全量，群的**成員組成**可能明顯不同，即使群數一樣 |
  | R4 | **增量漂移未測** | 增量只移動中心、不重新分配舊點 | ⚠ 長期多批增量後，中心可能偏離實際成員分布，而且**不會有任何警訊** |
  | R5 | **切換會讓既有主題全部重來** | 技術 7 群／功效 5 群 vs 既有 5／8 | ⚠ workspace 3 已有人工命名的主題，切換後 topic_code 對不上，命名等於作廢。**必須先備份 topic state 與人工 labels** |
  | R6 | coherence 依賴 gensim | 算不出來時退回其他三項排序 | 可能選到次佳 lambda，不致失敗 |

  ### 建議的驗收方式

  R1／R2 需要**第二批不同領域的專利**才驗得了。若目前沒有，建議先在拋棄式
  workspace 以 feature flag 試跑，確認流程完整後再決定是否切換正式 workspace
  （R5 的備份是切換前的必要前置）。
