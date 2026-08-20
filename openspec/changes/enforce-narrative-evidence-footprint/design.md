# Design

## 現況證據

全部取自 2026-08-20 job `ai:narrative` #452 的實測，非推論。

### 這次解讀確實一次都沒查資料庫

| 查證項目 | 來源 | 結果 |
|---|---|---|
| 工具呼叫組成 | CLI transcript `b0e95a6b-c052-420e-b131-2ac5c72c7dff.jsonl` | 35 次：Read 16、Grep 15、Glob 2、Write 2 |
| MCP 取證工具呼叫 | 同上，統計 `tool_use` 區塊 | **0 次** |
| `mcp__` 字串出現位置 | 同上，逐欄位路徑統計 | 只在 `attachment.addedNames`（41）與 `addedLines`（41）——**工具可用性宣告，非呼叫** |
| 是否讀過取證說明 | 同上，03:29:44 | 讀過 `prompts/data_access.md`——不是不知道有這條路 |

transcript 與 #452 的對應以兩項獨立事實確認：job result 的 `narratives_path` 與 transcript
最後寫入的檔案完全同一個；transcript 末筆時間 11:46:35 對上 job 完成時間 11:46:53。

### 四層護欄各自的失效方式

| # | 落點 | 現況 | 失效方式 |
|---|---|---|---|
| 1 | `ai_narrative_runner.py:535-546` | 派工提示詞宣告形狀為 `based_on_version` ＋ `reports` 兩層，**無 `evidence`**；`:478` 另聲明「附加需求不得凌駕輸出契約」 | 與 `report-narrative-flow.md:51-69`（v5 取證階段、evidence 足跡）直接矛盾；文件 `:74` 又自稱 evidence 為「選填、additive」。模型讀完兩份後照契約做 |
| 2 | `handlers.py:1093-1102` | `result` 為寫死 8 欄白名單 | runner 回傳 12 欄，`query_audit`／`query_count`／`contract_warnings`／`narratives_expired` 未列舉即丟棄，寫進 DB 的只有 8 欄 |
| 3 | `validate_narrative_contract` | 只走訪 narratives 內已存在的變體、驗三件套字數與形狀 | 結構上不檢查頂層 `evidence`，缺席不可能被察覺 |
| 4 | `test_narrative_requires_research.py:59-63` | `assertIn('"query_audit"', NARRATIVE_RUNNER.read_text())` | 對 runner **原始碼字串**斷言。runner 檔內有該字串即綠；從不讀 `handlers.py`、不跑流程，整條鏈斷在 handler 它不會知道 |

實測落庫結果：`workflow_outputs` 該 run 的 `job_result:ai:narrative` 僅 8 個鍵
（`based_on_version`／`variants_narrated`／`variants_total`／`pending`／`cli_kind`／
`prompt_version`／`narratives_path`／`artifacts_uploaded`），與白名單完全一致。
`narratives.json` 頂層僅 `based_on_version` 與 `reports`，無 `evidence`。
`contract_warnings` 在 `handlers.py` 與 `static/index.html` 皆**零出現**。

⚠ 值得記下的教訓：破口不在「規則沒寫」。取證階段寫了、稽核設計了、守門測試也建了——
但**規則寫在文件、契約寫在提示詞、測試寫在字串比對**，三者沒有一處落在實際資料流上。

## 架構與資料流

取證稽核跨兩個行程，唯一交換媒介必須說清楚：

```
worker 行程                          CLI 子行程 → MCP server 子行程
─────────                          ──────────────────────────
run_narrative()
  └ query_audit_file()  ──設環境變數 PATENT_QUERY_AUDIT_PATH──▶ 繼承
      （建暫存 JSONL）                                    report_research._audit()
                                                          逐筆 append JSONL
  └ read_query_audit()  ◀──────任務結束讀回──────────────────┘
  └ finally: 刪暫存檔
      │
      ▼  summary（12 欄）
  handle_ai_narrative()
      │  ⚠ 破口 2：白名單只放行 8 欄
      ▼
  workflow_outputs.data_json（JSONB）   ← 唯一持久化落點
      │
      ▼  API
  前端 AI 任務卡                        ⚠ 破口：無 contract_warnings 顯示
```

**唯一交換媒介**：worker 與 MCP server 是不同行程，記憶體內的 `_QUERY_AUDIT` 彼此看不到，
故以 `AUDIT_PATH_ENV` 指向的暫存 JSONL 作為唯一交換媒介。此設計已存在且正確，本 change
不動它；破口在 JSONL 讀回**之後**的那一段。

**同一份知識只能有一個定義處**：
- 稽核開檔與讀回 SHALL 維持只定義於 `mcp_server.report_research`（`AUDIT_PATH_ENV`
  的定義處），呼叫端共用，不得複製第二份格式。
- `evidence` 的形狀是**同一份知識的兩個落點**：`report-narrative-flow.md` 的契約章節
  與 `build_prompt` 的形狀宣告。本 change 的做法是讓 `build_prompt` **只引用不重述**
  ——提示詞指向文件章節，形狀正文只留在文件一處。⚠ 這正是本次事故的成因：兩處各自
  演進成 v3 與 v5，而不一致本身不會報錯。

## 程式落點

| 破口 | 檔案 | 動作 |
|---|---|---|
| 1 | `backend/app/worker/prompts/report-narrative-flow.md` | 「輸出契約 v3（三件套）＋ v5 evidence」一節：`evidence` 由「選填、additive」改為必要欄位；維持「組版端會忽略它」的 additive 說明 |
| 1 | `backend/app/worker/ai_narrative_runner.py` `build_prompt` | 形狀宣告納入 `evidence`，並改為引用文件契約章節而非重述形狀；移除與文件矛盾的「不得凌駕輸出契約」措辭 |
| 3 | 同檔 `validate_narrative_contract` | 新增頂層 `evidence` 檢查：缺少／空物件／`query_count == 0` 各產生可辨識警告（只 append 警告，不 raise） |
| 2 | `backend/app/worker/handlers.py` `handle_ai_narrative` | `result` 補 `query_audit`、`query_count`、`contract_warnings` |
| 2 | `backend/app/static/index.html` | AI 任務卡新增契約警告顯示區 |
| 4 | `tests/test_narrative_requires_research.py` | 字串比對改為端到端行為驗證 |

⚠ `validate_narrative_contract` 需要知道 `query_count` 才能判斷「零取證」，而該值來自
稽核讀回。實作時 `query_count` 由 `run_narrative` 傳入驗證函式，**不得**讓驗證函式自行
去讀環境變數或稽核檔——否則稽核落點就有了第二個定義處。

## 測試對照

| Requirement / Scenario | 目標測試 |
|---|---|
| EXP-008 有取證且足跡完整 | `test_narrative_requires_research.py`（新增：evidence 完整時無警告） |
| EXP-008 完全未取證 / evidence 為空 | 同上（新增：缺席與空物件各產生警告、job 仍 succeeded） |
| EXP-008 組版端不受影響 | `test_chart_sections.py`（既有；確認缺 `evidence` 仍正常組版） |
| EXP-009 有查詢 / 零查詢不得省略欄位 | `test_narrative_requires_research.py`（新增：實跑 handler，斷言結果三欄存在） |
| EXP-009 稽核讀取失敗不得拖垮工作 | `test_mcp_query_audit.py`（既有，補讀取失敗案例） |
| EXP-010 有警告 / 無警告 | `test_api_ai_tasks.py`（結果欄位）＋ `test_api_frontend.py`／`test_frontend_js_syntax.py`（顯示區與 JS 語法） |

⚠ **行為守門的真 Red 驗證**：新測試必須在**人為移除 handler 白名單任一欄**時變紅。
這一步不可省——破口 4 的教訓正是「有測試」不等於「守得住」，寫完要實際證明它會紅。

## 輸出契約

`narratives.json` 頂層新增（形狀正文以 `report-narrative-flow.md` 為唯一來源，此處僅示意）：

```json
"evidence": {
  "<report_key>": [
    {"claim": "...", "queried": "...", "patent_ids": [95, 102, 117]}
  ]
}
```

`workflow_outputs.data_json`（`output_type = job_result:ai:narrative`）新增三欄：

```json
{
  "query_audit": [{"tool": "query_database", "rows": 42, "truncated": false, "status": "ok"}],
  "query_count": 1,
  "contract_warnings": ["本次解讀未取證（query_count=0）"]
}
```

⚠ 三欄一律存在。零值時為 `[]`／`0`／`[]`，**不得因為值為空而省略**——欄位缺席與
「查過但沒查到」在消費端無法區分，正是本次事故難以察覺的原因之一。

## 風險與回復方式

| 風險 | 評估 | 處置 |
|---|---|---|
| 提示詞加嚴後，模型為了湊 `evidence` 而做無意義查詢 | 中。本 change 只驗「有沒有」不驗「查得對不對」 | 警告文字明確寫「未取證」而非「查詢次數不足」，避免誘導湊數；品質留人工驗收。實跑數次後檢視 `query_audit` 分布 |
| 加嚴後解讀耗時上升 | 中。#452 的 CLI 段約 17 分鐘，取證會再增加 | 只出警告不擋，逾時仍由既有 `cli_timeout_seconds` 控制；實跑後檢視是否需調整逾時 |
| 前端顯示警告造成使用者誤以為報表不可用 | 低 | 警告與 `succeeded` 並存顯示，文案明確區分「可用但未取證」與「失敗」 |
| 既有 `narratives.json` 全部缺 `evidence`，重組版時大量警告 | 低。警告只在解讀當次產生，不在組版時產生 | 不回填既有檔；EXP-008 已明訂組版端忽略此鍵 |

**回復方式**：四項改動彼此獨立，可逐項 revert。因無 migration、無 schema 變更，
revert 後既有 `workflow_outputs` 的多餘欄位由消費端忽略即可，無資料清理需求。
前端為靜態檔，revert 後重新載入即生效。
