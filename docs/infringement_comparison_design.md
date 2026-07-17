# 侵權比對（Claim Comparison）設計 — 第一版草案

狀態：設計稿，待使用者審核後排實作（2026-07-15，尚未寫任何程式）。
定位：架構總結（`patent_tool_architecture_summary.md`）的最優先目標；
本文件把「主權項/獨立項侵權比對」落成可實作的流程、資料模型與 LLM 邊界。

> 免責定位：本功能是專利工程師的**輔助工具**，輸出是要素對照與差異說明草稿；
> 侵權與否的判定一律由人工做成，系統與 LLM 都不輸出法律結論。

---

## 1. 目標與範圍（第一版）

做什麼：

- 給定「比對標的」與「一組專利的權利要求」，產出**逐要素 claim chart**：
  每個 claim 拆成要素（elements），逐要素標記 對應/疑似對應/未對應/資訊不足，附證據與說明。
- 後端以 **all-elements rule** 彙總：任一要素「未對應」→ 該 claim 整體標「不落入（初判）」；
  全要素「對應」→「落入風險（初判）」；含「疑似/資訊不足」→「需人工」。彙總是規則，不是 LLM 判定。
- 結果進 analysis 框架保存，可匯出成報告素材（後接 PPT exporter）。

不做什麼（第一版明確排除）：

- 不做均等論（doctrine of equivalents）的自動判定——「疑似對應」只是提示人工去看。
- 不做 file wrapper / 禁反言、有效性分析。
- 不自動抓產品資料（比對標的由使用者提供文字）。
- 不做全庫自動掃描（成本不可控），一次比對是「一個標的 × 使用者選定的專利集合」。

## 2. 比對模式與輸入

兩種模式，第一版先做模式 A：

| 模式 | 標的 | 用途 |
| --- | --- | --- |
| A. 產品 vs 專利 | 使用者提供的產品/技術特徵描述（自由文字或條列） | FTO／被訴風險：自家產品對競品專利，或競品產品對自家專利 |
| B. 專利 vs 專利 | 另一件專利的 claim | 佈局重疊、迴避設計參考（第一版不做，schema 預留） |

輸入來源：

- **專利側**：`derived_layer.report_patent_base."比對用權利要求"`
  （已是 `COALESCE(獨立項, 主權項, 所有權利要求)`，refresh 時算好）。
  比對範圍記錄 `claim_source`：實際取到的是哪一欄（獨立項/主權項/所有權利要求），報告需標示。
- **專利集合**：沿用 app_layer 快照——`analysis_runs.analysis_type='infringement'`、
  `selected_patent_ids_json`（與報表同一套 filters → patent_ids 機制，可追溯）。
- **標的側**：使用者輸入的特徵描述，存進 `analysis_runs.parameters_json`
  （`target_name`、`target_description`、可選的條列 `target_features[]`）。

## 3. 流程（pipeline）

```text
① 取 claim 文本        report_patent_base.比對用權利要求（快照內每件專利）
② claim 切分           複用 backend/app/clustering/preprocessing.split_claim_segments()
                       （claim 編號偵測已在分群前處理實作並驗證過 407 筆）
③ 要素拆解（LLM）      每條 claim → elements[]（前言/要素逐項，保留原文 span）
④ 逐要素比對（LLM）    每個 element × 標的描述 → verdict + evidence + explanation
⑤ 規則彙總（後端）     all-elements rule → claim 級初判；claim 級 → 專利級彙總
⑥ 人工覆核             前端逐要素確認/改判，覆核紀錄另存，不覆蓋 AI 原始輸出
⑦ 輸出                 claim chart（表）＋ 風險摘要（LLM 草稿）→ 報告/PPT 素材
```

- ③④ 每次呼叫都記錄 `prompt_version`、`model`、輸入 hash、原始回應——與分群引擎的
  LLM 追溯要求一致。
- ④ 的 verdict 枚舉：`met`（字面對應）/ `arguably_met`(疑似，含均等提示) / `not_met` / `insufficient_info`。
- 語言：claim 可能是中/英/日/韓（獨立項欄是 KR,JP,US,CN,EP,IN）。第一版不翻譯，
  LLM 直接跨語比對，輸出說明用繁體中文；`insufficient_info` 涵蓋「語言無法確認」情況。

## 4. LLM 邊界（沿用架構既定原則）

- LLM 只做：要素拆解、逐要素對應判斷草稿、差異說明、風險摘要文字。
- LLM 不做：最終侵權判定、不寫 DB 正式欄位、不改 Raw/Core、不決定 all-elements 彙總。
- 輸出固定 JSON schema，後端驗證：verdict 在枚舉內、每要素有 evidence 引文、
  element 原文 span 必須真的出現在 claim 文本裡（防幻覺，字串驗證）、缺欄位就 reject 重試。
- 逐要素比對每則輸出附 `confidence` 與 `needs_review`；低信心自動標人工。

## 5. 資料模型（草案，migration 0006）

第一版最小落地：沿用 `analysis_outputs`（`output_type='claim_comparison'`，result_json）即可跑通；
正式表在確認流程後建，草案如下：

```text
app_layer.claim_comparison_runs      -- 一次比對任務（FK analysis_id）
    run_id, analysis_id, target_name, target_description,
    mode ('product_vs_patent'|'patent_vs_patent'), prompt_version, model,
    status, created_at, completed_at

derived_layer.claim_elements         -- 要素拆解結果（可跨 run 重用，以 claim 文本 hash 為鍵）
    element_id, patent_id, claim_number, claim_source, claim_text_hash,
    element_index, element_text, is_preamble, prompt_version, model

app_layer.claim_element_findings     -- 逐要素比對結果
    finding_id, run_id, element_id, verdict, confidence,
    evidence_text, explanation, needs_review,
    review_status ('unreviewed'|'confirmed'|'overridden'), reviewer_verdict, reviewed_at

app_layer.claim_comparison_summary   -- claim/專利級彙總（規則算出，可重算）
    run_id, patent_id, claim_number, claim_verdict, patent_verdict, summary_text
```

原則不變：AI 原始輸出與人工覆核分欄保存；彙總可由 findings 重算；不動核心表。

## 6. 與現有系統的接點

- **analysis 框架**：`create-analysis --type infringement` 已有 CLI 參數，快照機制直接複用。
- **報表引擎**：統計型引擎不適合（需要 AI 逐件呼叫），比對走獨立 runner
  （`backend/app/comparison/` 新模組），輸出仍登錄 `analysis_outputs`/`export_runs` 保持追溯一致。
- **claim 切分**：`split_claim_segments()` 從 clustering.preprocessing 抽出共用
  （或 comparison 模組直接 import，避免複製邏輯）。
- **狀態欄**：比對前可用 `legal_status` 正規化（`mappings/legal_status.py`）過濾——
  對死掉的專利做 FTO 沒有意義，預設只比 alive，可由參數放寬。
- **PPT**：claim chart 是 PPT exporter 的第一個表格型素材；風險摘要是文字型素材。

## 7. 成本與批次控制

- 一次 run 的 LLM 呼叫量 ≈ 專利數 ×（1 次拆解 + 要素數次比對，比對可整條 claim 一次批走）。
- 要素拆解結果以 `claim_text_hash` 快取重用：同一 claim 不重拆。
- run 參數提供 `max_patents` 上限與 dry-run（只估算呼叫量不真呼叫）。

## 8. 開放問題（待使用者定案）

1. **LLM 供應商與 API**：Claude API（架構文件原定）/ 公司內部 LLM？API key 管理方式？
2. **模式 A 的標的輸入格式**：自由文字就好，還是要求條列特徵（條列可讓逐要素比對更準）？
3. **比對範圍**：只比獨立項（比對用權利要求現況）夠不夠？要不要引入附屬項（所有權利要求欄只有 JP,KR,CN 有值）？
4. **一次 run 的規模預期**：通常幾件專利？（影響是否需要佇列/斷點續跑）
5. **claim chart 的 PPT 版型**：一 claim 一頁？一專利一頁？（影響 exporter 素材切法）
6. **正式表 vs JSON**：第一版直接建 0006 正式表，還是先用 analysis_outputs JSON 跑通再定表？
