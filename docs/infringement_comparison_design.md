# 案件比對（Claim Comparison）第一版定案流程

狀態：產品流程已於 2026-07-20 定案；實體 DB schema、PDF 版型與第一版資料規模仍待使用者確認。
定位：獨立於報表／PPT 的案件比對 PDF 產線。本文件是案件比對流程、輸入、人工閘門、圖片資產與 LLM 邊界的依據。

> 免責定位：本功能是專利工程師的**輔助工具**，輸出是要素對照與差異說明草稿；
> 侵權與否的判定一律由人工做成，系統與 LLM 都不輸出法律結論。

---

## 1. 目標與範圍（第一版）

做什麼：

- 給定「比對標的」與「一組專利的權利要求」，產出**逐要素 claim chart**：
  每個 claim 拆成要素（elements），逐要素標記 對應/疑似對應/未對應/資訊不足，附證據與說明。
- 後端以 **all-elements rule** 彙總：任一要素「未對應」→ 該 claim 整體標「不落入（初判）」；
  全要素「對應」→「落入風險（初判）」；含「疑似/資訊不足」→「需人工」。彙總是規則，不是 LLM 判定。
- 結果進 analysis 框架保存，經人工覆核後由案件比對 PDF exporter 產生可對外交付的獨立文件；不依賴報表或 PPT 流程。

不做什麼（第一版明確排除）：

- 不做均等論（doctrine of equivalents）的自動判定——「疑似對應」只是提示人工去看。
- 不做 file wrapper / 禁反言、有效性分析。
- 不自動抓產品資料（比對標的由使用者提供文字、條列特徵、照片與結構圖）。
- 不做全庫自動掃描（成本不可控），一次比對是「一個標的 × 使用者選定的專利集合」。

## 2. 比對模式與輸入

兩種模式，第一版先做模式 A：

| 模式 | 標的 | 用途 |
| --- | --- | --- |
| A. 產品 vs 專利 | 使用者提供的產品/技術特徵描述（自由文字或條列） | FTO／被訴風險：自家產品對競品專利，或競品產品對自家專利 |
| B. 專利 vs 專利 | 另一件專利的 claim | 佈局重疊、迴避設計參考（第一版不做，schema 預留） |

輸入來源：

- **專利側**：所有種類專利都優先讀完整「所有權利要求」；後備才使用「獨立項＋從屬項」。不得再使用舊的 `COALESCE(獨立項, 主權項, 所有權利要求)` 順序。資料不足時標記缺口，不得只用主權項假裝完整。
- **權利範圍**：先辨識全部獨立項與從屬引用鏈；有幾項獨立項就分析幾項。獨立項任一必要要素不成立時，其分支從屬項可依 all-elements rule 推論不成立；獨立項成立或不確定時，才分析從屬項新增限制。
- **專利文字邊界**：Claim 理解只使用權利要求欄位，不從專利說明書抽取文字。結構關係不清楚時才按需從專利 PDF 抽圖。
- **專利集合**：由使用者明確選定，業務追溯以既定專利號機制對齊。
- **標的側**：`target_name`、`target_description`、條列 `target_features[]`，以及使用者提供的產品照片、結構圖或 CAD 圖。

## 3. 流程（pipeline）

```text
① 選定專利             使用者明確選擇要理解與比對的專利
② 取得權利要求         所有權利要求優先；後備為獨立項＋從屬項
③ Claude 專利理解      辨識全部獨立項、從屬引用鏈、技術要素與關鍵 Claim 用語
④ 使用者理解閘門       顯示專利理解稿；使用者修改或核准，未核准不得進產品比對
⑤ 按需抽圖             只對結構關係不清楚的要素從 PDF 選頁、裁圖並由使用者確認
⑥ 輸入產品資料         文字、條列特徵、照片、結構圖或 CAD 圖
⑦ 逐要素比對           Claude 產 verdict、專利證據、產品證據與 explanation 草稿
⑧ 規則彙總             後端依 all-elements rule 產 Claim／專利級結果
⑨ 人工覆核             使用者確認或改判；不得覆蓋 AI 原始輸出
⑩ 獨立 PDF 輸出        摘要、權利範圍、產品說明、Claim 解釋、分析、結論與 Claim Chart
```

- 專利理解稿必須先由使用者核准；核准內容以 Claim 文字 hash 鎖定，原文改變即重新確認。
- verdict 枚舉：`met`（對應）/ `arguably_met`（有爭議）/ `not_met`（未對應）/ `insufficient_info`（資料不足）。
- 語言：claim 可能是中/英/日/韓（獨立項欄是 KR,JP,US,CN,EP,IN）。第一版不翻譯，
  LLM 直接跨語比對，輸出說明用繁體中文；`insufficient_info` 涵蓋「語言無法確認」情況。

## 4. LLM 邊界（沿用架構既定原則）

- LLM 只做：要素拆解、逐要素對應判斷草稿、差異說明、風險摘要文字。
- LLM 不做：最終侵權判定、不寫 DB 正式欄位、不改 Raw/Core、不決定 all-elements 彙總。
- 輸出固定 JSON schema，後端驗證：verdict 在枚舉內、每要素有 evidence 引文、
  element 原文 span 必須真的出現在 claim 文本裡（防幻覺，字串驗證）、缺欄位就 reject 重試。
- 逐要素比對每則輸出附 `confidence` 與 `needs_review`；低信心自動標人工。
- all-elements rule 固定為：任一必要要素 `not_met` → 該 Claim 不成立；全部 `met` → 可能成立；含 `arguably_met` 或 `insufficient_info` → 需人工確認。規則由程式執行，不交給 Claude 自由判斷。

## 5. 資料與圖片保存原則

- 舊草案的多張 `claim_*` 表、`run_id`、`element_id`、`finding_id` 與中間處理時間設計已被 2026-07-20 最小追溯原則取代；正式 migration 前另行展示最小 schema。
- 業務對齊使用專利號；AI 原始理解／比對與人工覆核分欄保存；彙總可由 findings 重算；不動 Raw/Core 原始資料。
- 圖片只在必要時抽取，全部集中於 `data/patent_assets/<patent_number>/<pdf_sha256>/`，不得散落到 `output`、`tmp`、`data/raw` 或 workspace 目錄。
- DB 只保存最終選用圖片的相對路徑陣列，例如 `figure_paths_json`；不保存圖片 hash、頁碼、圖號、理由、狀態、時間或 binary。
- `source.pdf`、contact sheet、完整頁與裁切圖均留在上述專利資產目錄；不同 workspace 共用同一份專利資產。

## 6. 與現有系統的接點

- **獨立流程**：案件比對不依賴報表、PPT 或分群；分群只能協助使用者選件。
- **獨立輸出線**：統計型報表引擎不適合（需要 AI 逐件呼叫），比對走獨立 runner
  （`backend/app/comparison/` 新模組），結果依待確認的最小 comparison schema 保存，並由專用 PDF exporter 產檔。
- **claim 切分**：`split_claim_segments()` 從 clustering.preprocessing 抽出共用
  （或 comparison 模組直接 import，避免複製邏輯）。
- **PDF**：claim chart、逐要素證據、人工覆核結果與風險摘要組成案件比對 PDF；此輸出不經報表或 PPT exporter。

Claim Chart 固定包含：Claim／要素原文、Claim 解釋、專利證據、產品證據、verdict 與說明。第一版 PDF 章節固定為：案件與標的、權利範圍、產品說明、Claim 解釋、各獨立項分析、必要時的從屬項分析、風險摘要、結論及 Claim Chart 附錄。

## 7. 成本與批次控制

- 一次案件任務的 LLM 呼叫量 ≈ 專利數 ×（1 次拆解 + 要素數次比對，比對可整條 claim 一次批走）。
- 要素拆解結果以 `claim_text_hash` 快取重用：同一 claim 不重拆。
- 任務參數提供 `max_patents` 上限與 dry-run（只估算呼叫量不真呼叫）。

## 8. 開放問題（待使用者定案）

1. 完整「所有權利要求」與從屬項文字／引用關係要由哪一個匯入來源提供；目前 932 筆 DB 的所有權利要求皆空，現有 407 Excel 只有獨立項文字與從屬項數量。
2. 產品資料是否強制使用條列 `target_features[]`，或允許只交自由文字。
3. 一次案件通常選幾件專利，作為 job 批次、暫停續跑與前端分頁依據。
4. PDF 版型採一件專利一節，或一個獨立 Claim 一節。
5. 最小 comparison schema 採單一版本化寬表，或採一個 header＋一個 JSON detail 的輕量結構。
6. PDF 產生套件與中文字型；新增依賴前須由使用者確認。
