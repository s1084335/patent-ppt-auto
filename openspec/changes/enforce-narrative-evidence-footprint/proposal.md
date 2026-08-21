# Proposal: 解讀取證足跡與稽核可見化（enforce-narrative-evidence-footprint）

## Intent

讓「`ai:narrative` 這次有沒有實際取證」成為**可觀察且會現形**的事實，而不是只能事後翻
開發機 transcript 才推得出來的推論。

本 change 不改變取證的**能力**（工具早已掛好、範圍守門已由
`scope-narrative-evidence-to-workspace` 完成），只補三個讓「沒取證」得以靜默通過的破口。

## Why

2026-08-20 job `ai:narrative` #452 完成後實測（證據逐項見 design「現況證據」）：

| 應該留下的痕跡 | 實際 |
|---|---|
| MCP 取證工具呼叫 | **0 次**（35 次工具呼叫全是 Read／Grep／Glob／Write） |
| `narratives.json` 頂層 `evidence` | **欄位不存在** |
| job result 的 `query_audit`／`query_count` | **欄位不存在** |
| job result 的 `contract_warnings` | **欄位不存在** |

而該 job 的對外表現是 `status=succeeded`、`variants_narrated=17/17`、`pending=[]`、零警告
——從任何一個介面看都是完美通過。11 張報表 17 個變體的要點，全部只依 `report_data.json`
的聚合數字與 SVG 上的文字標籤寫成。

破口不在模型不聽話，而在**四層護欄全部只驗字面、沒有一層驗行為**：

1. 派工提示詞宣告的輸出形狀裡沒有 `evidence`，且明文聲明契約優先於文件要求；
   流程文件自己又把 `evidence` 寫成「選填」。模型讀過取證地圖後合理地選擇不做。
2. `handlers.py` 的結果白名單漏掉 `query_audit`／`query_count`／`contract_warnings`
   三欄，稽核與警告都到不了 DB。
3. `validate_narrative_contract` 只驗三件套字數與形狀，結構上不檢查 `evidence`。
4. 守門測試 `test_narrative_requires_research.py` 是對 runner **原始碼做字串比對**
   （`assertIn('"query_audit"', source)`），runner 檔案裡有那個字串就綠，
   從不看 `handlers.py`、也不跑實際流程。

⚠ 第 4 點是根因中的根因。只修 1～3，下次換別的地方斷掉一樣沒有人會知道。

## Scope

1. **輸出契約**：`evidence` 由選填升為解讀輸出契約的必要欄位；派工提示詞的形狀宣告
   必須與流程文件一致，不得再出現「文件要求／契約沒有」的矛盾。
2. **違規現形**：缺少 `evidence`、`evidence` 為空、或本次查詢數為零時產生契約警告。
3. **稽核落庫**：`query_audit`、`query_count`、`contract_warnings` 隨工作結果寫入
   `workflow_outputs`。
4. **警告可見**：前端 AI 任務卡顯示 `contract_warnings`。
5. **守門改為行為驗證**：把字串比對測試換成端到端測試，斷言整條鏈（runner → handler →
   工作結果）真的帶得到稽核欄位。

## Non-goals

- **不把零取證升級為失敗**。本 change 只出警告，`ai:narrative` 仍回 `succeeded`
  （Confirmed Decisions 1）。是否升級為硬性閘門，等累積數次實跑分布後另案裁決。
- 不改變取證工具清單、`query_database` 的 workspace 範圍守門，或 MCP server 行為
  （屬 `scope-narrative-evidence-to-workspace`，已完成）。
- 不評判取證**品質**（查得夠不夠深、引用得對不對）。本 change 只確保「查了沒有」
  與「查了什麼」成為事實，語意品質留給人工驗收。
- 不改報表引擎、圖表、分群、DB schema。
- 不重跑或修補 #452 的既有產出（Confirmed Decisions 3）。

## Impact

- **worker**：`ai_narrative_runner.build_prompt` 的形狀宣告；
  `validate_narrative_contract` 新增 evidence 檢查；`handlers.handle_ai_narrative`
  的結果欄位白名單。
- **提示詞**：`prompts/report-narrative-flow.md` 的「輸出契約 v3（三件套）＋ v5 evidence」
  一節，把 `evidence` 從「選填、additive」改為必要欄位。
- **前端**：`static/index.html` 的 AI 任務卡新增契約警告顯示區。
- **測試**：`test_narrative_requires_research.py` 由字串比對改為行為驗證。
- **DB**：**不需要 migration**。`query_audit`／`query_count`／`contract_warnings`
  存進既有 `app_layer.workflow_outputs.data_json`（JSONB），無 schema 變更、
  無資料搬移、無 derived refresh、無重匯需求。
- **相容性**：`evidence` 對組版端仍為 additive（`refresh_index` 與 PPT 端忽略該鍵），
  既有 `narratives.json` 不需回填；缺少時只產生警告，不影響既有報表顯示。
- **rollback**：四項改動彼此獨立，可逐項 revert；因無 migration，revert 後既有
  `workflow_outputs` 資料仍可讀（多出的欄位由消費端忽略）。

## Activation

合併後對**新派工的** `ai:narrative` 立即生效，無需部署額外服務、無需重啟 DB。
既有已完成的 job 結果不追溯補欄位。前端顯示需重新載入頁面（靜態檔）。

## Acceptance Gate

動工前先記錄測試基線。逐項驗收，未執行與不適用要分開揭露：

1. **契約**：派工提示詞的形狀宣告含 `evidence`，且與 `report-narrative-flow.md`
   的取證章節無矛盾（逐字比對兩處文字）。
2. **警告**：以無 `evidence` 的 `narratives.json` 實跑，`contract_warnings` 出現
   可辨識的未取證訊息，且 job 仍為 `succeeded`。
3. **落庫**：查 `app_layer.workflow_outputs` 該 run 的 `job_result:ai:narrative`，
   `query_audit`、`query_count`、`contract_warnings` 三欄存在；零查詢時
   `query_count` 為 `0`、`query_audit` 為 `[]`（**不得省略欄位**）。
4. **前端實物**：AI 任務卡實際顯示該警告（截圖或實機檢視，不接受「應該會顯示」）。
5. **行為守門**：`test_narrative_requires_research.py` 在人為移除 handler 白名單任一欄
   時**變紅**（真 Red 驗證，證明它守得住）。
6. **回歸**：直接測試＋整合測試＋契約測試三層指名檔案執行（清單見 tasks）。
7. **OpenSpec**：strict validation 通過。
8. **實跑對比**：修完後重跑 #452 的同一份 `based_on_version`，與 #452 的產出對比
   （Confirmed Decisions 3）。

## Confirmed Decisions

1. **2026-08-20**：`evidence` 定為**必填，但只出警告不擋**。缺少 `evidence`、
   `evidence` 為空或 `query_count == 0` 時寫入 `contract_warnings`，`ai:narrative`
   仍回 `succeeded`。理由：符合 `ai_narrative_runner` 既有註解「目前只回報不阻擋
   ——解讀是逐報表的，部分報表確實可能不需要額外查證；是否升級成硬性要求，
   等實跑數據看清楚分布再定」。
2. **2026-08-20**：實作落在既有分支 `feat/sync-report-contracts`（sync-work worktree），
   不另開分支。⚠ 已向使用者揭露此舉違反 workflows.md「一個 change 對應一個工作分支
   與一個 PR」，代價是該分支 PR 會混入不相干改動、審查與回溯變難；使用者裁決照此執行。
3. **2026-08-20**：#452 的既有產出**保留不動**，修完後重跑同一 `based_on_version`
   作為修法有效性的對比證據。
4. **2026-08-20（規劃時判定）**：`contract_warnings` 一併納入本 change。
   決策 1 選了「只出警告」，而實測顯示警告目前到不了 DB 也到不了前端
   （`handlers.py` 與 `index.html` 皆無此字串）——不修這條，「只出警告」等於什麼都沒做。
   故它是決策 1 的必要條件，不是額外擴張。

## Open Questions

無阻塞問題。以下為**非阻塞**、留待實跑數據後另案裁決：

- 累積數次實跑後，零取證是否由警告升級為 job 失敗（決策 1 刻意延後）。
- 是否需要「每張報表各自的取證覆蓋率」而不只是整份的 `query_count`。
- 其他 worktree 與主 repo `專案\專利_ppt自動` 有同樣四個破口，何時併入（決策 2 只涵蓋
  `feat/sync-report-contracts`）。
