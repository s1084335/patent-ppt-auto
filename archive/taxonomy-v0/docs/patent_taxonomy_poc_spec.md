# 分類樹 PoC 規格書

狀態：歷史封存（2026-08-17）。本文件不再作為目前專利專案待辦或實作依據。
設計依據：`patent_taxonomy_design.md`（2026-07-10 版）。

封存原因：P4 的 PatentSBERTa 嵌入路線與 P5 的骨架/box 訓練路線屬 taxonomy-v0
舊方案，已不接續目前分類/報表主線。文字保留供追溯，不代表可直接動工。

原定位：本文件是「設計 → 可執行」的工程規格：每階段定義輸入/處理/輸出/驗收，全部 CLI-first、可重跑。

---

## 0. 目的與範圍

**目的**：在正式語料上驗證「路線 B（骨架邊 joint loss，λ=0）＋frozen PatentSBERTa＋MLP projector」能否建出結構健康、語意合理的 手段/功效 兩棵 box 分類樹，並完成 800 件全量分類。

**PoC 做**：
```text
資料載入 → 切分/格式偵測 → 抽片語(兩件套) → 骨架+box 訓練(dim 網格) → 命名 → 全量分類 → 指標評估+人工看樹
```

**PoC 不做**（明確排除，避免蔓延）：
```text
✗ 不建任何資料庫表（tax_* 不建；產物全落檔案，PoC 過了才設計 migration 併入 derived_layer）
✗ 不動 raw/core 既有資料；不接前端；不做 workspace/實例
✗ 不做動態 loop（pending 重評/長節點/建議卡）—— 只驗「建樹+分類」
✗ 不用訊號語料（λ=0）；不跑 GPT 對照（gold set 於 v1 樹出來後才標）
✗ 不 fine-tune encoder（frozen；PoC 失敗才議 LoRA）
```

## 1. 前置條件

```text
資料      data/raw/TextDown_20260710_pm023742_525.xlsx（力山自有組合 525 筆＝正式語料）
          26 欄精簡匯出，MVP 欄位齊全；CN 306 筆為 WIPS 機翻英文（P3 抽查 CN/US 分層）
          407 園藝檔=分析集，不在 PoC 範圍
環境      本機 Windows、8GB RAM、無 GPU（夠用：MLP 很小、SBERT 推論 CPU 可跑，嵌入結果快取落盤）
          Python 3.12 via uv（--no-project --with 方式，不污染專案）
模型      sentence-transformers 載 AI-Growth-Lab/PatentSBERTa（首次下載 ~500MB，快取本機）
LLM       Claude API（管線用：補漏/裁決/同義/命名）；金鑰走環境變數，不入 repo
可重現    所有 LLM 呼叫 temperature=0；prompt 檔案化+版本號；隨機種子固定；設定檔版本化
```

## 2. 目錄與產物結構

```text
專案/專利_ppt自動/poc/taxonomy/
├── config/
│   ├── claim_formats.yaml      # 格式偵測 pattern 表（表驅動，§P2）
│   ├── stop_phrases.yaml       # claim 套語/效果模板清洗清單
│   ├── hyperparams.yaml        # dim 網格/margins/權重/門檻設定
│   └── prompts/                # LLM prompt 模板（含版本號）
├── p1_load.py … p8_evaluate.py # 管線腳本（見 §P1~P8）
└── out/                        # 全部產物（JSONL/CSV/JSON；git 不追蹤大檔）
    ├── patents.jsonl           # P1
    ├── claims_split.jsonl      # P2
    ├── phrases.jsonl           # P3 詞庫
    ├── patent_phrase.jsonl     # P3 專利↔片語↔維度
    ├── embeddings.npy          # P4（歷史封存；目前不產）
    ├── skeleton.json / boxes.npz / tree.json   # P5~P6（歷史封存；目前不產）
    ├── classification.jsonl    # P7
    └── report/                 # P8 指標報告+樹狀輸出
```

## 3. 管線階段規格

### P1 資料載入與欄位抽取
```text
輸入  data/raw/*.xlsx
處理  讀取 → 取欄位：独立项/效果 摘要/独立项数量/权利要求的项数/三號碼欄
      patent_no = COALESCE(授權公告號, 未審查的公開號, 申請號)
      異常標記：独立项数量<=0、切分數≠数量（idx 9/272/13 筆那類）→ flag，不剔除
輸出  patents.jsonl（每行：patent_no, means_text, effect_text, counts, flags）
驗收  筆數=來源筆數；flag 清單與既知異常吻合
```

### P2 獨立項切分與格式偵測
```text
輸入  patents.jsonl
處理  独立项 → 「 | 」切分 → 去開頭編號 → 容錯格式偵測（claim_formats.yaml 表驅動：
      轉折詞家族/標的類型/結構/Markush 排除/長 token 優先）
      → 前言(主題 metadata) | 主體(手段來源)；每項存 format_tag
      全 miss → LLM 切 + grounding 驗證 + flag='llm_split'
輸出  claims_split.jsonl（patent_no, claim_no, preamble, subject_type, body, format_tag）
驗收  切分數 vs 独立项数量 一致率 ≥ 99%（既知 3 筆例外）；llm_split 率 < 5%
      （首跑同時輸出格式分布報告 = 之前說的那次格式掃描）
```

### P3 抽片語（兩件套）
```text
輸入  claims_split.jsonl(手段=body) + patents.jsonl(功效=effect_text)
處理  手段：spaCy NP chunking 產候選 → stop_phrases 清洗/正規化/字面去重
           → LLM 每件一次 call（補漏含 L4 選擇性、裁決候選、標同義組；JSON 輸出）
           → grounding 子字串驗證
      功效：效果模板框剝殼 → LLM 抽為主（動賓型；grounding 照守）+ 依存句法動賓驗證
      粒度：L2+L3、嵌套收（嵌套對另存 nested_pairs 供訓練）、L1/L5 不收
      去重：字面自動 → 向量雙門檻(τ 起始 0.95/0.85，灰區 LLM 判) → canonical+alias
輸出  phrases.jsonl（phrase_id, canonical, aliases, dimension(s), sources, nested_parent）
      patent_phrase.jsonl（patent_no, phrase_id, dimension, source_field）
驗收  ★人工抽查 20 件：片語 precision ≥ 80%、明顯漏抽 ≤ 2 處/件（不過→啟用備援件再跑）
      grounding 失敗率 < 2%；每片語可追溯原文位置
```

### P4 嵌入
```text
狀態  已封存（2026-08-17），不再作為目前待辦或實作入口
輸入  phrases.jsonl（canonical 形態）
處理  PatentSBERTa 批次嵌入（CPU、batch 小、結果快取落盤，重跑不重嵌）
輸出  embeddings.npy + phrase_id 索引
驗收  全詞庫覆蓋；抽 10 對已知近義/遠義片語看 cosine 合理性（sanity）
```

### P5 骨架與 box 訓練（核心）
```text
狀態  已封存（2026-08-17），骨架建構與 box 訓練不再作為目前待辦或實作入口
輸入  embeddings.npy + nested_pairs + patent_phrase.jsonl
處理  ① 骨架：每維度分開 —— 多組階層聚類（agglomerative/spherical k-means × 不同 K/種子）
         → 共識邊（低共識留白）→ 骨架 v0（目標深度 2~3 層）
      ② 訓練配對：⟨片語→葉節點⟩⟨子節點→父節點⟩正對（嵌套對加入正對）
         + 兄弟/叔堂負對（BoxTaxo 採樣）
      ③ box 訓練：projector(MLP_c/MLP_o+exp) 接 frozen 嵌入
         L = α(Lg±)+β(Lp±)+γLr（公式=design §2.1；λ=0）
         超參：dim 網格 {8,16,32} 各跑；δ=0.05/ε=−0.03/φ=0.03、α1/β0.1/γ1、
              AdamW lr=1e-3、epochs≤100、batch=100、早停 by 驗證 loss
      ④ 迭代精修 1 輪：box 空間 R_a 重疊圖 + Directed Leiden → 修骨架 → 再訓
輸出  skeleton.json（v0 與精修後）、boxes.npz（三個 dim 各一組）、training_log
驗收  歷史驗收口徑，已封存；不得用來要求目前 agent 實作 P5
```

### P6 節點命名與節點盒
```text
輸入  精修後骨架 + 每節點成員片語
處理  LLM 命名（片語群→可讀技術標籤；prompt 附父節點脈絡求粒度一致）
      節點盒 = projector(節點標籤文字)（B 案）
      → 檢核：節點盒對成員片語的平均 R_a —— 過低（蓋不住成員）記錄警示（升級混合式的依據）
輸出  tree.json（node_id, label, parent, box, member_phrases, dimension）
驗收  ★人工看樹：兩棵樹各抽 10 節點，標籤可讀且成員一致 ≥ 7/10
```

### P7 全量分類與門檻校準
```text
輸入  tree.json + boxes + patent_phrase.jsonl
處理  全詞庫片語 → 對節點算 R_a → 排序式掛節點（argmax+margin、同分取體積小）
      分位數校準：已知片語 max R_a 分布 → τ_novel=P5、τ_class 由真邊分數分布定（歷史口徑，已封存）
      專利 = 片語節點聯集（多標籤、雙維度）
輸出  classification.jsonl（patent_no, node_id, dimension, score）+ thresholds.json + 未掛清單
驗收  coverage（片語掛上率）≥ 70%；每件專利雙維度至少各一標籤的比例 ≥ 90%
```

### P8 評估與報告
```text
輸入  以上全部產物
處理  指標（全腳本、三個 dim 對比）：
      reconstruction acc / containment 平均 R_a / volume 單調率 / 兄弟盒分離度 /
      NPMI coherence / diversity / 單子節點率 / 冗餘 / coverage / novel 率
      人工項：樹狀全文輸出（縮排文字＋每節點成員與計數）供人工審閱
輸出  report/metrics.json + report/tree_means.txt / tree_effects.txt + 一頁結論 md
驗收  見 §4
```

## 4. PoC 驗收與中止條件

**通過線（v1 候選成立，進入 gold set 標註與後續）**
```text
① 管線端到端可重跑（同輸入同輸出，LLM 步驟除快取重放）
② 最佳 dim 的 reconstruction acc ≥ 60%（錨：BoxTaxo Science 60.9%）
③ 人工看樹 ≥ 7/10 節點合理（兩維各自）
④ coverage ≥ 70% 且樹深 ≥ 2 層（兩維各自）
```

**中止/轉向線（觸發即停，回設計層對症）**
```text
recon acc < 40% 且超參調整無效   → frozen encoder 疑撐不起 → 議 LoRA/解凍（design §11-B）
樹長不出 ≥2 層                   → 片語量/粒度檢討；考慮撈訊號語料
節點盒蓋不住成員（P6 警示普遍）   → 節點盒升級混合式（設計已留路）
人工看樹 < 5/10                  → 骨架品質問題 → 聚類參數/共識門檻重調
```

**成本預算**
```text
LLM：P3 每件 1 call ×800 + P2 llm_split 少量 + P6 命名 ~100 節點級 → 數美元級
機器：嵌入+訓練 CPU 可跑（MLP 小、三個 dim 各 <30min 級）；總計一兩晚批次
人工：P3 抽查 20 件(~1hr) + P6 看樹(~1hr) + P8 報告審閱
```

## 5. 執行順序與檢核點（歷史封存）

```text
原規劃：P1→P2（首跑附格式分布報告）→ P3 →★人工抽查 20 件(過才續)→ P4→P5→P6 →★人工看樹→ P7→P8 →★PoC 報告
現況：P4/P5 已封存，原規劃不得當作目前待辦；後續若重啟分類樹，需另開新規格重定 pipeline。
```

## 6. 與正式系統的關係

- 產物全在 `poc/taxonomy/out/`，**不碰四層資料庫**；PoC 通過後另立 migration 設計把詞庫/樹/分類併入 `derived_layer`（tax_* 表，見 design §12）。
- prompt/設定檔/門檻起始值：PoC 定稿後原樣升級為正式管線資產。
- 中止條件觸發時：回 `patent_taxonomy_design.md` §11 對應待決項，不在 PoC 內擴大改動。
