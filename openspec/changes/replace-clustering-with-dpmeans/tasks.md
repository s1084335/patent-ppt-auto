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
  - **API**：無需改動。DP-Means 主題走既有 topic_state_json 通道；關鍵詞由
    `clustering/keywords.py` 自行抽取（class-TF-IDF），既有契約測試
    （`test_api_topics_keywords_contract`）通過。
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
  - **品質指標照常計算**：⚠ 本項原本寫「DP-Means 沒有 c-TF-IDF，指標只能填 None」
    ——**那是錯的**。coherence／diversity 只需要每群 top_terms、不綁 BERTopic，補上
    `clustering/keywords.py` 後兩個既有指標完全適用；balance／small_topic_ratio 本就
    只看件數分布。指標算不出來時（缺文件、長度對不上）才回 None，且排序時視為最差。
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

  判準（及格制，任一不過即淘汰）＋ 四指標加權排序（沿用 `model.RANKING_WEIGHTS`，
  不複製第二份）。判準與門檻見 `engine._CRITERIA`。

  > ⚠ **2026-08-09 事後新增第五項判準 `single_cluster`，交使用者裁決**：
  > 判準是量測**之後**加的，依 AGENTS.md 必須明說。
  >
  > **不是為了讓結果好看而調門檻**，是原判準有漏洞：判準③（群間最小距離）在
  > 只有 1 群時算不出值、回 `None`，而 `None` 被當成「通過」。實測合成資料
  > （5 個明顯分開的群、40 篇）時，選出的竟是「**1 群 40 篇**」而且**分數最高**
  > ——diversity 對單一群回 1.0（沒有重疊是因為沒有第二組可比）。
  >
  > **影響**：滑雪機兩通道的 λ 與群數**完全不變**（0.9064／7 群、0.9573／5 群），
  > 掃描表中被新判準刷掉的列為 0。⚠ 這個缺陷在這批資料上不會發生，
  > 在別批會——正好印證 R1「只驗一批資料會漏掉東西」。

  ### 兩次被實測推翻的公式

  | 版本 | 公式 | 技術（n=35） | 功效（n=44） | 結果 |
  |---|---|---|---|---|
  | v1 | 最近鄰距離 P75 | 18 群、9 單點 | 25 群、15 單點 | ⚠ 碎裂。根因是**衡量的量選錯**——最近鄰回答「最近的鄰居有多近」，建群門檻要回答的是「一個群的半徑該多大」 |
  | v2 | 全體距離 P25／P33 | 數字對得上 | 數字對得上 | ⚠ 倒果為因，且固定分位等於假設所有批次適用同一半徑 |
  | **現行** | **每批自動掃描選擇（24 點）** | λ=0.9093、7 群 | λ=0.9342、6 群 | 兩通道各自選出不同 λ——這才是「各自適合」 |

  ### 最終結果（workspace 3）

  | 通道 | n | λ | 群數 | 各群件數 | coherence | diversity | balance | 加權分 |
  |---|---|---|---|---|---|---|---|---|
  | 技術（獨立項） | 35 | 0.9093 | 7 | 10,7,7,5,4,1,1 | — | — | — | — |
  | 功效（效果摘要） | 44 | 0.9342 | 6 | 12,12,7,5,4,4 | — | — | — | — |

  對照 1.1 基準（MiniBatchKMeans 技術 5／功效 8）。執行時間 6.4s／1.1s（24 點）。
  掃描原始資料留在 `output/_verify/dpmeans/`。

  ### ⚠ 掃描點數：原值 18 沒有依據，實測取樣不足（交使用者裁決）

  `SWEEP_STEPS = 18` 是我憑感覺定的。使用者問「18 個點怎麼算出來的」才去實驗：

  | 點數 | 技術群數 | 功效群數 | 技術耗時 |
  |---|---|---|---|
  | 6／9／12 | 5／6／7 | 5／5／5 | 1.3／1.5／2.3s |
  | **18（原）** | 7 | **5** | 3.2s |
  | 24／36／60 | 7／7／7 | **6／6／6** | 6.4／14.6／12.5s |

  ⚠ 功效通道在 18→24 從 5 群跳到 6 群，而 24 以上一致——**18 點的結果不是
  收斂值**。先前報的「功效 5 群」是掃太疏的產物。改為 24（群數開始穩定的起點）。

  交叉驗證：合成資料（已知 5 群）在 24／36／60 都正確選出 5 群。

  ⚠ 判準是「**群數在點數增加時是否穩定**」，不是 24 這個定值。換一批資料若群數
  持續變動，要再往上調。
- [x] 3.2 執行 clustering/topic/API 目標測試、相關模組回歸與 `scripts/verify_module.py`

  | 項目 | 結果 |
  |---|---|
  | 目標測試（11 支 DP-Means／排序測試檔） | **136 過**，0 紅 |
  | 範圍回歸（`-k dpmeans/topic/clustering/handler/artifact/keyword/candidate/ranking`） | **614 過、1 紅** ——`test_default_report_names_match_definitions`，⚠ **既有失敗**（`DEFAULT_REPORT_NAMES` 12→13，PR #19 合併時即存在），非本輪造成 |
  | `verify_module.py` 功能測試 | **136 過**（首輪 119） |
  | `verify_module.py` 靜態分析 | ✅ **新增行 0 個**（首輪 5 個；全庫既有 59 個另計） |
  | `verify_module.py` 圈複雜度 | ⚠ 剩 1 支：`runner._persist_final_topics` C(20)——⚠ **既有函式，原本是 D(27)**，抽出共用尾段後降 7。降到 ≤10 要大改與本 change 無關的 BERTopic 邏輯，不在本輪範圍 |
  | `verify_module.py` 覆蓋率 | 87%（門檻 90%）——⚠ **純函式模組全部達標**：`dpmeans` 100%、`keywords` 100%、`engine` 100%、`artifacts` 100%、`model` 94%。缺口全在 `runner`(17%)／`workspace_service`(28%) 的 DB 接線行，逐行說明見下 |

  ### 覆蓋率未達標的逐行說明

  未覆蓋的 55 行**全部**需要真實 DB 連線（`psycopg.connect`）。它們是組裝與落庫
  的接線碼，決策邏輯已全數抽成純函式並個別測到：

  | 檔案／行段 | 內容 | 覆蓋方式 |
  |---|---|---|
  | `runner` 721–736 | calibrate 的引擎分流與 DP-Means 分支 | 3.3 端到端（兩通道各跑一次） |
  | `runner` 798–841 | finalize 的引擎分流 | DP-Means 分支：3.3 端到端；KMeans 分支：既有 DB 測試（本機無 postgres 已排除） |
  | `runner` 1104–1288 | `_calibrate_with_dpmeans`／`_finalize_with_dpmeans` 本體 | 3.3 端到端；其內的計算全在 `engine.plan_*`（100% 覆蓋） |
  | `workspace_service` 808–821 | 增量分流與 artifact 狀態存回 | 3.3 增量段實測 |
  | `workspace_service` 1403–1482 | 指派寫入、新主題落地、centroid 計算 | 3.3 增量段實測；決策邏輯在 `engine.plan_topic_keys`／`build_topic_entries`（100% 覆蓋） |

  ⚠ 這些行**不是沒驗**——是驗在端到端而非單元測試層。首輪報告只寫「需 DB」時
  我還沒跑增量，那句話當時是不成立的（見 3.3 的增量段）。
- [x] 3.3 產出 cluster artifact、run metadata、技術/功效 topic labels 與代表性 UI/API 結果，確認重跑可再現

  以**拋棄式 workspace** 跑完整 calibrate→finalize（使用者定案：不碰 workspace 3
  的人工命名），驗收後已清除。腳本：`scripts/verify_dpmeans_end_to_end.py`。

  | 項目 | 技術（獨立項） | 功效（效果摘要） |
  |---|---|---|
  | 主題數／指派 | 7／35 | 5／44 |
  | 各群件數 | 10,7,7,5,4,1,1 | 13,13,9,6,3 |
  | artifact | `algorithm=dpmeans`，7 中心 | `algorithm=dpmeans`，5 中心 |
  | λ | 0.906421 | 0.957287 |
  | run metadata | 值＋推導方法＋18 列掃描表 | 同左 |
  | 主題完整性 | 7/7 有關鍵詞、`label_source=fallback`、代表專利 | 5/5 同左 |

  **可再現性**：λ 與 3.1 掃描算出的值**完全相同**（0.906421／0.957287）。

  ### 增量段（CLU-004，第二輪補驗）

  ⚠ 首輪只驗了 calibrate→finalize，**增量路徑完全沒實測**——而「增量長出新主題」
  正是本 change 的目的。第二輪改為先用 55 筆專利 finalize，再補進保留的 5 筆跑
  增量。

  | 項目 | 技術 | 功效 |
  |---|---|---|
  | finalize 群數 | 9 | 10 |
  | 增量處理文件數 | 3 | 5 |
  | 增量後中心數 | 9（未減少） | 10（未減少） |
  | 增量 λ | 0.839201（＝artifact 記錄值） | 0.802280（＝artifact 記錄值） |
  | **新主題** | **0** | **0** |

  ⚠ **新主題建立的路徑端到端沒被觸發**：補進的 5 筆專利都落在既有主題附近。
  這不是缺陷（新主題本就該在真有新技術方向時才出現），但要如實說——那條路徑
  目前只有單元測試覆蓋（`NewTopicDetectionTests`、`test_dpmeans_new_topic_persistence`
  共 22 支）。要端到端驗它，需要一批**技術方向確實不同**的專利。

  ⚠ **同時實證了 R3**：少 5 筆專利（60→55），λ 從 0.906 變 0.839、群數從 7 變 9。
  每批資料自己決定 λ 是設計本意，但也代表**資料量變動會讓主題結構明顯改變**。

  ### ⚠ 實機驗收抓到三個純函式測不出來的介面不符

  | # | 問題 | 症狀 |
  |---|---|---|
  | 1 | 驗收腳本在連線前沒載入 `.env` | 連到預設 `localhost:5433`（兩天前就停的容器），逾時，**看起來像 DB 掛了**。⚠ `.env` 載入原本藏在 `runner.py` 的 import side effect 裡 |
  | 2 | DP-Means 候選缺 `candidate_k`、`k_scan` 落錯層 | 我憑印象寫成 `k`；`k_scan` 該在 `metrics.k_scan`。⚠ 後者**不會報錯**，只是候選列表空白 |
  | 3 | `LambdaSelection` 缺 `sample_size` | `build_run_metadata` 讀它，AttributeError 直到 finalize 才炸 |

  三者根因相同：**接線層沒有測試覆蓋**。已補三支契約測試搬到單元測試層
  （`test_dpmeans_candidate_contract.py`、`MetadataCompatibilityTests`）。
- [ ] 3.4 記錄未測規模與效能風險，經使用者確認群組品質與回復方案後才 archive

  ### 未測與風險（2026-08-09 如實記錄）

  | # | 風險 | 現況 | 影響 |
  |---|---|---|---|
  | R1 | **真實資料的通用性樣本不足** | 只驗過 workspace 3 的兩個通道（35／44 件，滑雪機領域）。**合成資料**已補測（見 R2 表）：8 個真實群在 n=100／300／600／1000 全部正確選出 8 群 | ⚠ 合成資料的群是乾淨分離的，真實專利文本沒那麼理想。**不同技術領域仍未測**——判準門檻是在滑雪機這批上定的 |
  | R2 | **大規模效能**（已量測） | n=100→10.4s、300→13.8s、600→24.5s、1000→46.1s（皆正確選出 8 群） | n≤1000 可接受（校準本就是耗時流程，舊引擎掃 7 組 k 每組跑一次 BERTopic 更慢）。⚠ **n≥5000 未測**，外推約 4 分鐘以上；屆時需要縮減掃描點數或改用向量化實作 |
  | R1b | **判準本身可能還有漏洞** | 已補 `single_cluster`（見 3.1 的裁決說明）——原判準在單群時整組失效，只驗滑雪機一批完全看不到 | ⚠ 同類漏洞（某個判準在邊界情形算不出值 → 被當成通過）可能還有。目前四項都已檢查 None 的處理，但只有實際跑過不同分布的資料才驗得出來 |
  | R3 | **順序敏感的長期效應** | 判準④只保證「換順序群數變動 ≤1」 | DP-Means 先看到的點決定中心起點。⚠ 新增專利後重跑全量，群的**成員組成**可能明顯不同，即使群數一樣 |
  | R4 | **增量漂移未測** | 增量只移動中心、不重新分配舊點 | ⚠ 長期多批增量後，中心可能偏離實際成員分布，而且**不會有任何警訊** |
  | R5 | **切換會讓既有主題全部重來** | 技術 7 群／功效 5 群 vs 既有 5／8 | ⚠ workspace 3 已有人工命名的主題，切換後 topic_code 對不上，命名等於作廢。**必須先備份 topic state 與人工 labels** |
  | R6 | coherence 依賴 gensim | 算不出來時退回其他三項排序 | 可能選到次佳 lambda，不致失敗 |

  ### 建議的驗收方式

  R1 需要**第二批不同領域的專利**才驗得了。合成資料已補測（8 個真實群在四種
  規模下全部正確找到），但那是乾淨分離的群，真實專利文本沒那麼理想。

  ### 回復方案（切換前必讀）

  | 情境 | 做法 | 為什麼可行 |
  |---|---|---|
  | 完全不切換 | 不設 `CLUSTERING_ALGORITHM` | 預設就是舊引擎（`test_default_is_kmeans` 釘住） |
  | 已切換、想回舊引擎 | 拿掉 flag → 重新 calibrate + finalize | ⚠ 舊 run **不會被刪**（0021 append-only，一律建新版本），隨時查得到 |
  | 已有 DP-Means artifact 的 workspace | 拿掉 flag **不影響它們** | 增量跟隨 artifact 記錄的演算法，不看 flag（`test_incremental_ignores_feature_flag`）——這是刻意的，中途換引擎會讓中心格式對不上 |
  | 保住人工命名 | 切換前匯出 `topic_state_json->'topics'` | ⚠ **必要前置**：DP-Means 的 topic_code 與舊主題對不上，人工命名等於作廢 |

  ⚠ 回復不是「按一個鈕還原」——重新 finalize 會產生新的主題編號，下游報表與
  簡報都要重產。這是換分群演算法的本質代價，不是這個實作的缺陷。
