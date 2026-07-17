# Classification Engine Design

本文件定義第一版專利文檔分類引擎。此設計只處理 BERTopic 文檔分群與兩層 topic hierarchy，不實作舊版分類樹，也不把分類結果寫回 `raw_layer` / `core_layer`。

> **★ 2026-07-15 範圍修訂（使用者裁決，優先於本文其餘章節；細節見 `.agents/context/decisions.md`）**
>
> 1. **分群只做一層**：本文所有「第 2 層／遞迴拆分」章節（doc_ratio≥0.25、coherence P25 拆分規則、`max_depth=2`、recursive_split）**作廢不實作**；主題的合併/細分**留給使用者判斷**，前端提供合併操作。
> 2. **按 workspace 區分，global 第一版不做**：每個 workspace 都有自己的「技術（`wips_independent_claims`）＋功效（`effect_summary`）」兩組分群——workspace 建立時初次分群，後續新資料進該 workspace 走**各自的 incremental**（不重跑全量）；`global` scope 的 schema/CLI 參數保留但第一版不實作排程，之後要開直接用。
> 3. **前端呈現**：比照 WIPS「分類标签」面板——標籤名＋件數 chips（如 `Noise Reduction (25)`），可勾選**套用到列表篩選**，含 Unclassified/Other 桶與使用者排序。
> 4. **LLM**：topic 命名（草稿、人可改）＋topic 摘要**引擎照跑照存，前端暫不顯示**；不可決定 assignment、不覆蓋分群、不自選 candidate（原邊界不變）。
> 5. 其餘定案不變：c_v、PCA100D、三組候選由使用者定案、MiniBatchKMeans／incremental 硬需求。

## 第一層目前實作狀態

`backend/app/clustering/runner.py` 已實作第一層的兩階段正式管線：

1. `calibrate`：讀取 DB 既有 PatentSBERTa embedding，只做一次 IncrementalPCA 100D，依序掃描 `k = 10, 15, 20, 25, 30, 35, 40`。
2. 每組計算 `c_v coherence`、topic diversity、balance、small topic ratio 與排序輔助 score。
3. 從低、中、高三段各選一組，保存為 `conservative`、`balanced`、`detailed` 候選，run 停在 `needs_review`。
4. `finalize`：只有收到使用者選定的 `candidate_id` 後，才重跑該 k 並寫入第一層 topics 與 assignments。

```powershell
uv run python -m backend.app.clustering.runner calibrate --scope global
uv run python -m backend.app.clustering.runner calibrate --scope workspace --workspace-id 123
uv run python -m backend.app.clustering.runner finalize --run-id 7 --candidate-id 14 --selected-by user@example.com
```

校準不會自動選候選，也不會寫入 topics／assignments。三組候選的 LLM 短解釋屬於後端 LLM service；目前尚未建立 API client，因此 `llm_explanation` 保持 `NULL`，不能宣稱已接通。

## 目標

- 用 BERTopic + `MiniBatchKMeans` 對專利文本做主題分群。
- 支援 `global` 全資料主分類與 `workspace` 特定資料分類。
- `workspace` 是正式產品物件，用來保存使用者指定的一組專利集合與後續 incremental 匯入。
- 第一版只做兩層：
  - 第 1 層：全體文件主題。
  - 第 2 層：只對過大或 coherence 差的第 1 層 topic 再拆。
- 對 `獨立項` 與 `效果摘要` 分別建模，不混在同一個模型裡。
- 以 `topic coherence`、`topic diversity`、`balance` 選擇候選主題數與遞迴拆分結果。
- 保存 run、profile、topic、assignment、quality metrics 與 label，讓 PPT 與後續分析可追溯。

## 非目標

- 不建立完整分類樹或 box embedding taxonomy。
- 不讓 LLM 決定正式分群 assignment。
- 不直接修改專利核心資料。
- 不把模型管線綁死特定 WIPS 檔、固定 300 筆資料或固定 workspace。
- 第一版不追求無限遞迴；超過第 2 層先停止。

## 系統邊界

分類引擎分成兩層責任：

```text
Backend orchestration
  - 決定 scope: global / workspace
  - 決定 patent set
  - 建立 topic run
  - 管理 model profile / incremental lineage
  - 呼叫 clustering pipeline

Clustering pipeline
  - 讀取指定 patent_id + source_field 的文本
  - 建 embedding
  - 跑 BERTopic + MiniBatchKMeans
  - 搜尋候選 k
  - 計算 quality metrics
  - 決定第 1 層 topic 與必要的第 2 層 topic
  - 輸出 topics / assignments / keywords / representative docs / metrics
```

`global` 與 `workspace` 不應寫死在模型內。模型只接收明確輸入與參數。

## 資料來源

正式分類資料來源只讀資料庫，不直接讀 Excel / WIPS 原始檔。Excel 檔只用於開發期前處理驗證；目前第一層 corpus provider 從 `core_layer.patents` 讀文本，並以 `core_layer.patent_embeddings.patent_id` 重用向量。

第一版支援兩種 `source_field`：

```text
wips_independent_claims    WIPS 獨立項
effect_summary             效果摘要
```

`wips_independent_claims` 只允許讀 DB 欄位 `core_layer.patents."獨立項[KR,JP,US,CN,EP,IN]"`。不讀 `比對用權利要求`，也不 fallback 到 `主權項`、`所有權利要求` 或其他 claim 全文欄位。若獨立項為空，該文件不進入此 source_field 的 corpus，不用其他 claim 欄位補值。

實際取文邏輯：

- `wips_independent_claims`：讀 `core_layer.patents."獨立項[KR,JP,US,CN,EP,IN]"`。
- `effect_summary`：讀後續 corpus provider 明確定義的效果摘要 DB 來源。
- 空值、過短文本、重複文本要在 corpus provider 標記，不直接丟給模型。

輸入文件結構：

```text
doc_id               run 內部文件 id
patent_id            core_layer.patents.patent_id
source_field         wips_independent_claims / effect_summary
text                 模型輸入文本
text_hash            清理後文本 hash
metadata_json        國別、申請人、日期、授權公告號等追溯欄位
```

## 模型管線

第一版固定使用：

```text
Embedding model       PatentSBERTa
Reducer               IncrementalPCA (100D)
Topic model           BERTopic
Cluster model         MiniBatchKMeans
Vectorizer            CountVectorizer
Topic representation  c-TF-IDF + top keywords
```

`PatentSBERTa` 名稱、權重 hash 與執行參數寫入 `patent_embeddings` 與 `topic_runs.parameters_json`。若部署環境不能連外下載模型，模型檔應放在正式 artifact storage 或容器 image 內；runtime 只讀本機模型。

專利號追蹤沿用目前資料庫四欄機制，優先順序為：`授權公告號`、`審查的公告號`、`未審查的公開號(轉換後)`、`申請號(轉換後)`。WIPS 的未審查公開號與申請號原值保留在原欄，緊鄰的 `(轉換後)` generated columns 是所有下游唯一使用值。後端不主動加上 `US-`、`TW-` 等國別前綴；`country_code = TW` 且號碼以四位有效西元年開頭時，前四碼減 1911 並輸出三位民國年，後方內容原樣保留，例如 `2024123456` 轉成 `113123456`。非 TW 的轉換後值等於原值。Embedding、代表性專利、前端與 PPT 都使用轉換後的 `patent_number / patent_number_type`。

依 Hugging Face API 查詢，`AI-Growth-Lab/PatentSBERTa` repository 約 `438.73 MB` decimal / `418.40 MiB`，其中主要權重 `pytorch_model.bin` 約 `438,022,897 bytes` / `417.73 MiB`。正式部署時模型檔放後端容器或容器可讀的 artifact 路徑，不在執行時反覆連外下載。

使用者確認：`PatentSBERTa` 可以先下載下來包進模型/後端容器。容器 build 階段應固定模型來源、路徑與 hash；runtime 只從本機路徑載入，不在服務啟動或分類任務執行時連 Hugging Face。

## 文本前處理

文本前處理是分類管線的一部分，需版本化為 `preprocessing_version`。第一版使用保守策略：不重排文本、不摘要、不翻譯，只做不影響文本架構的格式清理；超過模型上限時才做 claim-aware chunking。

允許的前處理：

```text
unicode_normalization = NFKC
normalize_newlines = true
trim_outer_whitespace = true
collapse_inline_spaces = true
preserve_paragraphs = true
remove_invisible_control_chars = true
decode_html_xml_entities = true
remove_export_noise = conservative
min_text_chars = 50
deduplicate_exact_text = true
track_truncation = true
```

保守處理：

```text
preserve_claim_numbering = true
preserve_bullets = true
preserve_punctuation = true
preserve_numbers = true
preserve_technical_symbols = true
```

禁止處理：

```text
translation = false
summarization = false
sentence_rewrite = false
claim_element_reorder = false
keyword_only_replacement = false
```

獨立項可能超過 `PatentSBERTa` 的 tokenizer 長度限制；正式管線使用通用 claim-aware chunking，不綁死單一資料檔或單一欄位內容。切分規則：

```text
1. 先偵測文本中的 claim 編號，例如 1. / 8. / 15.
2. 以完整 claim 為最小單位。
3. 依序貪婪合併：若 claim 1 + claim 2 <= max_seq_length，就放同一 chunk；若超過，claim 1 單獨成 chunk，再嘗試 claim 2 + claim 3。
4. 只有單一 claim 自身超過 max_seq_length 時，才允許在該 claim 內部依 token 切分。
5. 每個 chunk 各自 embedding，再用 weighted_mean 合成 patent-level embedding。
```

管線必須保存截斷與 chunk metadata：

```text
raw_text_hash
model_text_hash
token_count
max_seq_length
was_truncated
would_truncate_without_chunking
would_truncate_after_chunking
was_chunked
chunk_count
chunk_claim_numbers
chunk_token_counts
chunk_texts             runtime embedding input；預設不寫入一般 audit JSON
split_within_claim_count
chunking_strategy = claim_aware_greedy
aggregation_method = weighted_mean
truncation_policy = chunked_no_truncation
```

若後續要改前處理規則，必須升級 DB 的 `preprocessing_version`，例如 `patent_text_clean_v2`；舊 embedding 與舊 run 不覆蓋。

前處理核心模組是 `backend/app/clustering/preprocessing.py`。此模組不負責讀 Excel、不負責決定 workspace，也不是正式分類任務入口；它只接收上游已選定的文本值，輸出清理後文本、chunk text 與 audit metadata。正式管線後續應由 corpus / DB 讀取層取出 `patent_id + source_field + text`，再呼叫此模組。

目前不保留 `preprocess_cli.py`。若之後上線後需要新的開發期檢查工具，再另開入口，但不能把正式資料來源混成讀 Excel / WIPS 檔。

Embedding 與後續 BERTopic / MiniBatchKMeans 模型邏輯放在 `backend/app/clustering/model.py`，不另外拆一個單純的 embedding module。`model.py` 目前先負責載入 SentenceTransformer-like model、逐 chunk 產生 embedding，並用 token-count `weighted_mean` 聚合為 patent-level embedding。

模型開發與測試指令使用 `uv`，但不要求每次固定同一條 `uv` 指令或同一個臨時依賴環境；可依任務使用 `uv run`、`uv --with ... run` 或正式 `uv add ...`。正式依賴若要加入專案，使用 `uv add sentence-transformers scikit-learn`，不手改 lock 檔。

407 筆 WIPS 測試結果：

```text
檔案: data/raw/TextDown_20260714_pm122609_407.xlsx
tokenizer: AI-Growth-Lab/PatentSBERTa
max_seq_length: 512

wips_independent_claims:
  usable_count = 406
  skipped_empty_count = 1
  exact_duplicate_count = 0
  tokenizer_default_truncated_count = 157
  max_token_count = 2167

effect_summary:
  usable_count = 406
  skipped_empty_count = 1
  exact_duplicate_count = 13
  truncated_count = 0
  max_token_count = 75
```

Claim-aware chunking 測試結果：

```text
wips_independent_claims with claim-aware chunking:
  would_truncate_without_chunking_count = 157
  would_truncate_after_chunking_count = 0
  chunked_doc_count = 157
  split_within_claim_doc_count = 26
  split_within_claim_chunk_count = 65
  max_chunk_count = 6
  max_chunk_token_count = 512
  aggregation_method = weighted_mean
```

因此 `effect_summary` 可先固定使用 `patent_text_clean_v1` 且不需 chunk。`wips_independent_claims` 使用 `patent_text_clean_v1 + claim-aware chunking + weighted_mean` 後，送進 `PatentSBERTa` 的每個 chunk 不會超過 512 tokens；其中 26 筆因單一 claim 自身過長，需要 claim 內 token split。

基本流程：

```text
1. Load corpus
2. Clean text
3. Build embeddings
4. Reduce embeddings with IncrementalPCA for clustering space
5. Optionally generate 2D projection for visualization only
6. For k in top_level_k_range:
     run BERTopic(cluster_model=MiniBatchKMeans(n_clusters=k))
     compute coherence / diversity / balance
     apply quality gate
7. Select best top-level run candidate
8. For each top-level topic:
     decide whether to split
     if split needed, search child k range
     apply child quality gate
9. Generate topic keywords, representative docs, labels
10. Write run, topics, assignments, metrics
```

降維不等於降到 2 維。407 筆固定 k 實測後，正式聚類空間固定使用 `IncrementalPCA(n_components=100)`；2D 只用於前端視覺化投影，不拿 2D 當正式聚類空間。100 維分群結果若要畫圖，另外投影成 2D/3D，但 topic assignment 仍以 100 維結果為準。

## 固定參數

以下參數由程式碼中的命名 config 管理，執行時完整快照到 `topic_runs.parameters_json`；同一個 incremental lineage 不得任意變動：

```text
source_field
embedding_model
preprocessing_version
min_text_chars
deduplicate_text
vectorizer_ngram_range
vectorizer_min_df
vectorizer_max_df
stop_words_version
ctfidf_parameters
dimensionality_reducer
random_state
batch_order_policy
quality_gate_version
label_prompt_version
```

固定這些參數的原因是避免每次重跑語意空間都變，導致 topic id、topic label 與 PPT 結論無法追溯。

## 可搜尋參數

`MiniBatchKMeans` 必須指定 `n_clusters`，所以主題數用候選範圍搜尋，不人工硬填單一值。

第 1 層候選：

```text
top_level_k_range 根據 n_docs 動態決定
```

第 2 層候選：

```text
child_k_range = 2..child_k_max
child_k_max = min(6, floor(parent_doc_count / min_child_docs))
```

第一版建議資料量規則：

```text
if n_docs < 100:
  top_level_k_range = 2..min(8, floor(n_docs / 8))
elif n_docs <= 500:
  top_level_k_range = 6..min(20, floor(n_docs / 15))
elif n_docs <= 3000:
  top_level_k_range = 8..min(40, floor(n_docs / 25))
else:
  top_level_k_range = 10..min(80, floor(n_docs / 40))
```

若上限小於下限，以上限為準並標記 `needs_review`，避免資料太少卻硬切很多主題。

可一起校準但不建議頻繁改動的參數：

```text
batch_size
n_init
init_size
reassignment_ratio
max_iter
max_no_improvement
```

第一版建議 profile 初始值：

```text
batch_size = 根據 n_docs 與 k 動態決定
n_init = 10
init = k-means++
init_size = min(n_docs, max(3 * k, 100))
reassignment_ratio = 0.0 或 0.01
random_state = 固定值
```

`MiniBatchKMeans.batch_size` 初始規則：

```text
batch_size = min(n_docs, max(128, 10 * k))
batch_size_cap = 1024
batch_size = min(batch_size, batch_size_cap)
```

對 300-500 筆資料，通常會落在 128-200 左右；資料上萬時才會逐步放大到 512 / 1024。

## 評估指標

第一版評估指標先精簡為四個：`coherence`、`diversity`、`balance`、`small_topic_ratio`。`score` 只作候選排序輔助，前端與報告不得只顯示 score，仍需同時顯示四個指標與代表性專利。

### Topic coherence

用途：判斷同一 topic 的 top keywords 是否語意一致。

第一版定案使用 **`c_v`**（2026-07-14）。每個 topic 得到一個 coherence，再用 topic 文件數加權平均：

```text
weighted_coherence = sum(topic_coherence_i * doc_count_i) / sum(doc_count_i)
```

### Topic diversity

用途：判斷不同 topic 的 top keywords 是否高度重複。

建議定義：

```text
topic_diversity = unique_top_keywords / total_top_keywords
```

例如每個 topic 取 top 10 keywords，若 10 個 topic 共 100 個 keyword，其中唯一詞 78 個，diversity = 0.78。

### Balance

用途：避免一個 topic 吃掉大多數專利，其他 topic 只剩碎片。

使用 normalized entropy：

```text
balance = -sum(p_i * log(p_i)) / log(k)
```

其中 `p_i` 是第 i 個 topic 的文件比例。越接近 1，代表分布越平均。

### Small topic ratio

用途：避免主題切得太碎，產生太多只有少量專利的小群。

建議定義：

```text
small_topic_ratio = small_topic_count / topic_count
```

其中 small topic 可先定義為 topic 文件數小於 `min_topic_docs`。

## 降維方案測試規則

近期測試重點是判斷正式聚類是否需要降維，因此先固定同一個 k，不做 `k_range` 搜尋。先比較三種 clustering space：

```text
768D      PatentSBERTa 原始 embedding，直接聚類
PCA100D   IncrementalPCA 100 維後聚類
PCA50D    IncrementalPCA 50 維後聚類
```

三種方案使用同一個固定 k、同一份 embedding、同一套 `MiniBatchKMeans` 參數，只比較降維對分群品質的影響。正式產品仍要恢復 `top_level_k_range` 與三組候選流程。

### 407 筆固定 k 實測結果（2026-07-14）

輸入為 WIPS `独立项[KR,JP,US,CN,EP,IN]`，407 列中 406 筆可用。使用本機 `AI-Growth-Lab/PatentSBERTa`、claim-aware chunking、token-count weighted mean、固定 `k=10` 與相同 MiniBatchKMeans 參數。678 個 chunks 全部在 512 tokens 內，沒有截斷。

| clustering space | c_v coherence | diversity | balance | small topic ratio | topic size 範圍 |
|---|---:|---:|---:|---:|---:|
| 768D | 0.3523 | 0.84 | 0.8720 | 0.00 | 5-104 |
| PCA100D | 0.3439 | 0.86 | 0.9848 | 0.00 | 25-56 |
| PCA50D | 0.3306 | 0.81 | 0.9357 | 0.00 | 13-98 |

目前暫定正式 clustering space 使用 `PCA100D`。它相較 768D 只降低約 0.0084 coherence，但 topic diversity、balance 與執行速度較好；PCA50D 的 coherence 與 diversity 都進一步下降，壓縮偏多。這個結論只回答「要不要降維」，不代表 `k=10` 已定案；正式產品仍需在 PCA100D 上執行動態 `top_level_k_range`、三組候選與使用者定案流程。

完整輸出：`output/clustering_dimension_test/dimension_comparison.json`。Patent-level embedding cache 為 `output/clustering_dimension_test/patent_embeddings.npz`，正式產品改存 `core_layer.patent_embeddings`，不以 NPZ 作正式向量庫。

## 第 1 層選模規則

每個候選 k 都計算：

```text
score =
  0.40 * normalized_coherence
+ 0.25 * topic_diversity
+ 0.25 * balance
- 0.10 * small_topic_ratio
```

`score` 是排序輔助，不是最終判決。系統可用 score 排出最值得看的候選，但前端與使用者決策仍需看到四個指標、topic size 分布與每群前 5 筆代表性專利。

硬門檻：

```text
topic_diversity >= 0.70
balance >= 0.55
max_topic_ratio <= 0.60
small_topic_ratio <= 0.30
min_topic_docs >= 5
```

選擇流程：

```text
1. 先移除未通過硬門檻的候選 k。
2. 在通過者中排序 score。
3. 產生 3 組 candidate parameter sets 給前端。
4. LLM 根據 metrics 短短解釋三組差異。
5. 使用者從三組中選一組作為 accepted candidate。
6. 使用者選定後才寫入正式 accepted topics / assignments。
7. 若全部失敗，標記 run 為 needs_review，不寫入正式 accepted 結果。
```

系統不自動把最高分候選直接定案。最高分只作為推薦排序，正式定案由使用者在前端確認。

## 三組候選參數組合

分類引擎每次校準或 full run 完成後，前端只呈現 3 組候選，不把所有 k 候選丟給使用者。

三組候選建議命名：

```text
conservative    主題較少，較穩定，適合 PPT 簡報與高層摘要
balanced        coherence / diversity / balance 綜合分數最佳或接近最佳
detailed        主題較多，較細，適合使用者想看技術細節
```

候選挑選規則：

```text
1. 從通過 hard gate 的候選 k 中建立候選池。
2. balanced 取 score 最高者。
3. conservative 從低於 balanced topic count 的候選中，取 score 接近且 topic 數較少者。
4. detailed 從高於 balanced topic count 的候選中，取 score 接近且 topic 數較多者。
5. 若某一類找不到合格候選，允許用相鄰候選補位，但前端需標示 reason。
6. 三組候選不得使用不同 embedding / preprocessing / source_field，只能差在 clustering granularity 與 MiniBatchKMeans 相關參數。
```

若通過 hard gate 的候選少於 3 組：

```text
1 組通過：只呈現 1 組，標示候選不足，建議重新校準。
2 組通過：呈現 2 組，第三組不硬湊。
0 組通過：整個 run = needs_review，不進入使用者選擇。
```

前端候選資料結構：

```json
{
  "candidate_id": "balanced_k12_v1",
  "candidate_type": "balanced",
  "display_name": "平衡版",
  "source_field": "wips_independent_claims",
  "parameters": {
    "n_clusters": 12,
    "batch_size": 128,
    "n_init": 10,
    "reassignment_ratio": 0.0
  },
  "metrics": {
    "coherence": 0.61,
    "diversity": 0.78,
    "balance": 0.68,
    "max_topic_ratio": 0.23,
    "small_topic_ratio": 0.12,
    "score": 0.69
  },
  "topic_count": 12,
  "estimated_leaf_topic_count": 16,
  "llm_explanation": "主題數中等，關鍵詞重複少，最大主題沒有過度集中，適合作為預設分類結果。",
  "recommended": true
}
```

三組候選直接存入 `topic_candidates`；使用者選定時更新該列的 `is_selected / selected_by / selected_at`，同一 run、同一 parent topic 最多只能有一組被選定。

## 第 2 層遞迴聚類訊號

第二層不是每個 topic 都拆，只拆「過大」或「coherence 差」的 topic。

### Parent 是否進入拆分候選

硬門檻：

```text
parent_doc_count >= min_parent_docs
depth == 1
```

建議初始值：

```text
min_parent_docs = 35
max_depth = 2
```

拆分訊號：

```text
should_consider_split =
  parent_doc_count >= min_parent_docs
  AND (
    parent_doc_ratio >= 0.25
    OR parent_coherence <= low_coherence_threshold
  )
```

建議初始值：

```text
parent_doc_ratio_large = 0.25
low_coherence_threshold = calibration 後由資料分布決定
```

`low_coherence_threshold` 不建議先寫死絕對值，初版可用同一 run 內 topic coherence 的第 25 百分位：

```text
low_coherence_threshold = percentile(topic_coherence, 25)
```

### Child split 候選

對 parent topic 內部文件搜尋：

```text
child_k_range = 2..min(6, floor(parent_doc_count / min_child_docs))
min_child_docs = 8
```

每個 child k 計算：

```text
child_score =
  0.50 * normalized_child_coherence
+ 0.30 * child_topic_diversity
+ 0.20 * child_balance
- penalty_small_child
- penalty_dominant_child
```

### Child split 接受條件

只有全部符合才接受拆分：

```text
weighted_child_coherence >= parent_coherence + 0.03
child_topic_diversity >= 0.70
child_balance >= 0.55
max_child_ratio <= 0.75
min_child_docs >= 8
child_k >= 2
depth + 1 <= 2
```

如果 child split 沒通過，parent topic 保持 leaf topic。

## Topic 狀態

每個 topic 需要標示是否可作為報告呈現：

```text
accepted        通過品質門檻，可用於 PPT/前端呈現
needs_review    指標不穩或候選分歧，需人工確認
rejected        候選模型或 child split 未通過，不作為正式 topic
superseded      被新版 run 或 profile 取代
```

Topic 也要標示：

```text
is_leaf_topic
parent_topic_id
depth
topic_path
```

PPT 第一版只呈現 leaf topics，必要時附上 parent topic 作為上層分類標題。

## LLM 使用邊界

LLM 可以做：

- topic label
- topic summary
- representative docs 摘要
- 判斷 label 是否可讀
- 短短解釋三組候選參數組合的差異，讓使用者理解 conservative / balanced / detailed 的取捨

LLM 不可以做：

- 決定 patent assignment
- 覆蓋模型分群結果
- 修改 core patent data
- 在沒有 metrics 的情況下強制接受 split
- 自行選定最終 candidate

LLM 解釋候選組合時，只能引用下列 evidence：

```text
candidate_type
topic_count
estimated_leaf_topic_count
coherence
diversity
balance
max_topic_ratio
small_topic_ratio
score
top_keywords sample
representative docs sample
```

前端呈現的 LLM 解釋應短，不做長篇報告：

```text
conservative: 主題較少，適合簡報，但可能把相近技術放在同一群。
balanced: 指標最平均，主題數與可讀性折衷，建議作為預設。
detailed: 主題較細，能看到更多技術差異，但小群比例較高。
```

LLM 輸出需保存：

```text
llm_model
prompt_version
input_topic_id 或 candidate_id
input_keywords
input_representative_docs
label
summary
confidence
rationale
created_at
```

使用者定案需保存：

```text
selected_candidate_id
selected_by
selected_at
selection_reason       nullable
```

Topic summary 的輸入基礎是每個 topic 的前 5 筆代表性專利。代表性專利由模型端依距離 centroid 最近產出；LLM 只讀這 5 筆與 topic keywords / metrics 做摘要，不自行挑專利。

3 組候選的 LLM 輔助解釋放在後端 LLM service，不放進 clustering model。模型端只產生三組候選、metrics、keywords sample、代表性專利 sample；後端 LLM service 負責把這些 evidence 寫成短解釋給前端。

## 資料庫設計草案

分類結果是衍生分析資料，建議放在 `derived_layer`；任務執行狀態與匯出仍沿用 `app_layer`。

Embedding 結果依使用者要求放在 `core_layer.patent_embeddings`，因為 embedding 會重複使用且屬於後續模型管線共用資料。但不得修改既有核心表架構。`core_layer.patents`、`core_layer.patent_sources`、`core_layer.patent_people`、`core_layer.patent_attributes` 等既有核心表不新增模型欄位、不重排、不回寫 embedding；embedding 只能進新表或外部儲存。

### app_layer.workspaces

用途：保存正式產品中的 workspace。Workspace 代表使用者指定的一組專利集合，用於獨立分類、後續 incremental 匯入與前端操作。

```text
workspace_id
workspace_name
description
status                  active / archived / disabled
filter_json             建立 workspace 時的篩選條件
parameters_json         workspace 顯示與操作參數
created_by
created_at
updated_at
archived_at
```

Workspace 是產品層物件，不是模型參數。分類 run 只引用 workspace，不負責定義 workspace。

### app_layer.workspace_patents

用途：保存 workspace 的專利成員清單。Workspace 第二次匯入或補資料時，新增成員進這張表，分類引擎再對新增 patent 做 incremental。

```text
workspace_id
patent_id
source_type             manual / import / filter / incremental_import
source_ref              匯入來源或篩選來源
added_by
added_at
```

### core_layer.patent_embeddings

用途：保存每篇專利、每個 source field、模型版本與文本版本的 embedding。`patent_id` 負責 FK，解析後的專利號負責業務追蹤；四種原始號碼留在 `core_layer.patents`，不在本表重複。

```text
embedding_id
patent_id                      FK core_layer.patents(id)
patent_number                  依優先順序解析出的追蹤專利號
patent_number_type             grant_publication_number / examined_publication_number / unexamined_publication_number / application_number
source_field
preprocessing_version
text_hash
embedding_model
model_version                  PatentSBERTa 權重 SHA-256
embedding_vector              PostgreSQL 使用 VECTOR(768)
chunk_count
aggregation_method
metadata_json                 token counts、weights、tokenizer、vector hash 與稽核資訊
created_at
```

`embedding_id` 只作技術主鍵。重用唯一鍵為 `patent_id + source_field + embedding_model + model_version + preprocessing_version + text_hash + aggregation_method`。每筆向量對應哪篇文檔，主要靠 `patent_id` join 核心表，前端與人工查找則使用 `patent_number / patent_number_type`。

使用者確認：DB 的 embedding table 就是向量庫，不另外外掛獨立 vector DB。PostgreSQL 開發期使用 `pgvector` 的 `VECTOR(768)`；正式 SQL Server 依公司版本確認 vector 欄位/索引能力後做等價 migration。若 SQL Server 版本不支援原生 vector index，仍維持同一張 embedding table 作為唯一向量存放表，再評估索引或相似度查詢替代方案。

### derived_layer.topic_runs

用途：保存一次 global/workspace 分類 run。

```text
run_id
workspace_id            nullable；NULL 代表 global，非 NULL 代表 workspace
source_field
run_mode                full / incremental
previous_run_id         nullable
status                  pending / running / completed / failed / needs_review
input_doc_count
new_doc_count
topic_count
parameters_json
metrics_json
model_artifact_path      IncrementalPCA、MiniBatchKMeans、BERTopic 狀態 bundle
model_artifact_hash
error_message
created_at
completed_at
```

模型參數直接快照在每個 run；incremental 所需物件打包為單一 artifact bundle，由 path/hash 追蹤，不另設 profile/artifact table。

### derived_layer.topics

用途：保存 topic hierarchy 與 topic-level metrics。

```text
topic_id
run_id
parent_topic_id
topic_code
depth
doc_count
coherence
diversity
balance
keywords_json
representative_patent_ids_json
label
summary
label_source            llm / manual
label_metadata_json
status
created_at
```

第一層 `parent_topic_id = NULL`，第二層指向第一層。Label 與前五筆代表性專利摘要直接存 topic，不另設 label table。

### derived_layer.topic_assignments

用途：保存 patent 到 topic 的分配結果。

```text
run_id
topic_id
patent_id
distance_to_centroid
created_at
```

主鍵為 `(run_id, topic_id, patent_id)`。同一專利可同時保留第一層與第二層 assignment；depth 與 source field 分別由 topic、run 取得。

### derived_layer.topic_candidates

用途：保存每個 run 或 parent topic 的三組候選、品質指標、LLM 短解釋與使用者選定結果。

```text
candidate_id
run_id
parent_topic_id         nullable, NULL 代表第 1 層候選
candidate_type          conservative / balanced / detailed
candidate_k
coherence
diversity
balance
score
parameters_json
llm_explanation
is_selected
selected_by
selected_at
created_at
```

資料庫以 partial unique index 保證同一 `run_id + parent_topic_id` 最多一組 `is_selected = true`。

## CLI 入口

第一版先做 CLI runner，之後再接 worker。

校準並輸出三組候選，不寫 accepted topics：

```powershell
uv run python -m backend.app.clustering.runner `
  --scope global `
  --source-field wips_independent_claims `
  --config-name default_wips_independent_claims_v1 `
  --mode calibrate `
  --emit-candidates 3
```

```powershell
uv run python -m backend.app.clustering.runner `
  --scope global `
  --source-field wips_independent_claims `
  --config-name default_wips_independent_claims_v1 `
  --mode full
```

Workspace：

```powershell
uv run python -m backend.app.clustering.runner `
  --scope workspace `
  --workspace-id <workspace_id> `
  --source-field effect_summary `
  --config-name default_effect_summary_v1 `
  --mode full
```

指定 patent set：

```powershell
uv run python -m backend.app.clustering.runner `
  --scope workspace `
  --workspace-id <workspace_id> `
  --source-field wips_independent_claims `
  --patent-ids-file data/workspace_patents.txt `
  --config-name default_wips_independent_claims_v1
```

使用者在前端選定後，由後端定案：

```powershell
uv run python -m backend.app.clustering.runner `
  --run-id <run_id> `
  --accept-candidate <candidate_id>
```

## Incremental 策略

`MiniBatchKMeans` 支援 `partial_fit`，但正式上線管線不能只保存 DB 結果，還必須保存模型 artifact 與 incremental lineage。第一版就要支援真正 incremental。

```text
full              全量建立或重建 run
incremental       用 previous_run_id 與 artifact bundle 處理新增文件
```

Incremental 寫入原則：

- 新資料匯入後，由後端更新 global 或 workspace 的 patent set。
- 後端建立新的 `topic_runs`，`previous_run_id` 指向上一版。
- 新增 patent set 由 workspace membership 與前後 run assignment 差異取得，不在 run 塞 patent id JSON。
- `model_artifact_path / model_artifact_hash` 指向 BERTopic、IncrementalPCA、MiniBatchKMeans、vectorizer 與 c-TF-IDF 狀態 bundle。
- incremental run 只對新增文件產生新 embedding，再用既有模型狀態做 partial update。
- 不覆蓋舊 assignment；新版 assignment 另存。
- 若新增資料比例太高，或 quality gate 失敗，標記 `needs_review`，不自動取代上一版。
- 若需重調 `top_level_k_range` 或主要參數，建立新的 full run，舊 run 保留不覆蓋。

建議觸發新的 full rebuild 的條件：

```text
new_doc_ratio >= 0.30
quality_status = failed
新增資料大量落在低信心 assignment
topic balance 長期惡化
使用者明確要求重算
```

## PPT 使用方式

PPT 第一版讀取：

```text
leaf topics
topic label / summary
doc_count / doc_ratio
top keywords
representative patents
每個 topic 的授權公告號、申請號、標題、申請人
```

PPT 不直接讀模型物件，也不重新計算分群。它只讀資料庫中已 accepted 的 `topic_runs` 與 leaf `topics`。

## 初版實作順序

1. 新增 clustering DB migration。
2. 新增 `backend/app/clustering/`：
   - `preprocessing.py`
   - `model.py`
   - `pipeline.py`
   - `metrics.py`
   - `recursive_split.py`
   - `writer.py`
   - `runner.py`
3. 補 Python 依賴：
   - `bertopic`
   - `sentence-transformers`
   - `scikit-learn`
   - `gensim` 或 coherence 指標所需套件
4. 實作 dry-run：只輸出 candidate metrics，不寫 accepted topics。
5. 用 300 筆資料校準 profile。
6. 通過後寫入正式 `derived_layer.topic_*` tables。
7. 接到 PPT exporter。

## 待確認問題

- ~~coherence 指標第一版使用 `c_v`、`u_mass`，或以 embedding-based coherence 取代~~ → **已定案 `c_v`（2026-07-14）**。
- PPT 呈現時是否同時顯示第 1 層 parent 與第 2 層 child，或只顯示 leaf topics。
