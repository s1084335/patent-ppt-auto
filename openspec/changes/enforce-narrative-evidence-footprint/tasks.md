# Tasks

依可獨立驗收的功能切片排列。切片 A 先做——沒有它，決策 1 選的「只出警告」到不了任何介面。

## 0. 基線

- [ ] 記錄測試基線：`uv run pytest tests/test_worker_handlers.py tests/test_narrative_requires_research.py tests/test_mcp_query_audit.py tests/test_api_ai_tasks.py tests/test_narrative_contract_v4.py -q`，
      把當下紅／綠數量寫進本檔，作為分辨「這次弄壞的」與「本來就壞的」的依據。
- [ ] 確認工作分支為 `feat/sync-report-contracts` 且已推到遠端並設定 tracking（決策 2）。

## 切片 A：稽核與警告落庫（EXP-009、EXP-010 後端）

- [ ] **Red**：在 `tests/test_narrative_requires_research.py` 加端到端測試——注入 fake
      `cli_runner` 寫出 `narratives.json` 與假稽核 JSONL，實跑 `handle_ai_narrative`，
      斷言回傳結果含 `query_audit`、`query_count`、`contract_warnings`。
      預期真實失敗原因：**handler 的 8 欄白名單未列舉這三欄，結果 KeyError／值為 None**。
- [ ] **Red**：加零查詢案例，斷言 `query_count == 0` 且 `query_audit == []`，
      兩欄仍存在（EXP-009 零查詢不得省略欄位）。
- [ ] **Green**：`handlers.py:1093-1102` 的 `result` 補三欄，最小改動，不動其他欄位。
- [ ] **Refactor（必要時）**：若白名單已難維護，評估改為明列「排除欄位」而非「放行欄位」，
      使未來 runner 新增欄位預設可見。⚠ 此項為選配，不得在本切片混入其他行為變更。
- [ ] **守門有效性驗證**：人為移除白名單任一欄，確認新測試**真的變紅**，再還原。
      這一步不可省——破口 4 的教訓正是「有測試」不等於「守得住」。
- [ ] 目標測試：`tests/test_narrative_requires_research.py`、`tests/test_worker_handlers.py`
- [ ] **停止點**：三欄能從 `workflow_outputs` 讀回即完成本切片，不接著改前端。

## 切片 B：警告對使用者可見（EXP-010 前端）

- [ ] **Red**：在 `tests/test_api_frontend.py` 加測試，斷言 AI 任務卡區塊含契約警告的
      顯示落點。預期真實失敗原因：**`index.html` 完全沒有 `contract_warnings` 字串**。
- [ ] **Green**：`static/index.html` 的 AI 任務卡新增警告顯示區；警告與 `succeeded`
      並存，文案區分「可用但未取證」與「失敗」。
- [ ] 契約測試：`tests/test_frontend_js_syntax.py`（改前端 inline JS 必跑）
- [ ] 目標測試：`tests/test_api_frontend.py`、`tests/test_api_ai_tasks.py`
- [ ] **停止點**：前端實機看得到警告文字即完成，不接著改提示詞。

## 切片 C：evidence 契約與檢查（EXP-008）

- [ ] **Red**：加契約測試——`narratives.json` 缺 `evidence`、`evidence` 為空物件、
      `query_count == 0` 三種情形各產生可辨識警告，且 job 仍 `succeeded`。
      預期真實失敗原因：**`validate_narrative_contract` 只走訪已存在的變體，
      結構上不檢查頂層 `evidence`，三種情形皆回零警告**。
- [ ] **Red**：加相容性測試——不含 `evidence` 的既有 `narratives.json` 仍能正常組版
      （EXP-008 組版端不受影響）。預期此測試**可能一開始就綠**；若綠，須確認它是有效測試
      （人為讓組版讀取 `evidence` 使其變紅）再保留。
- [ ] **Green**：`validate_narrative_contract` 新增頂層 `evidence` 檢查；
      `query_count` 由 `run_narrative` **傳入**，不得讓驗證函式自行讀環境變數或稽核檔
      （避免稽核落點出現第二個定義處）。
- [ ] **Green**：`prompts/report-narrative-flow.md` 的輸出契約章節，`evidence`
      由「選填、additive」改為必要欄位，保留「組版端會忽略它」的 additive 說明。
- [ ] **Green**：`ai_narrative_runner.build_prompt` 形狀宣告納入 `evidence`，改為
      **引用文件契約章節而非重述形狀**；移除與文件矛盾的「不得凌駕輸出契約」措辭。
- [ ] **一致性檢查**：逐字比對 `build_prompt` 產生的提示詞與 `report-narrative-flow.md`
      的取證章節，確認無第二處形狀正文（同一份知識只能有一個定義處）。
- [ ] 目標測試：`tests/test_narrative_contract_v4.py`、`tests/test_narrative_contract_point_shape.py`
- [ ] 整合測試：`tests/test_chart_sections.py`（組版鏈不因缺鍵而壞）
- [ ] **停止點**：三種違規情形都出得了警告即完成。

## 1. 範圍回歸

指名檔案，三層都要有；排掉無關領域（launcher、import、clustering）：

- [ ] 直接測試：`tests/test_worker_handlers.py`、`tests/test_narrative_requires_research.py`、
      `tests/test_mcp_query_audit.py`、`tests/test_narrative_contract_v4.py`、
      `tests/test_narrative_contract_point_shape.py`
- [ ] 整合測試：`tests/test_chart_sections.py`、`tests/test_cluster_reports_and_narrative.py`、
      `tests/test_per_report_narrative_rerun.py`、`tests/test_ai_narrative_runner.py`
- [ ] 契約測試：`tests/test_api_ai_tasks.py`、`tests/test_api_frontend.py`、
      `tests/test_frontend_js_syntax.py`
- [ ] 紅了先分辨歸屬：用 `--tb=line` 歸類根因、`git log -S` 追來源，別把既有債算進本次改動，
      也別因「不是我弄的」就放著擋住驗收。

## 2. 交付前驗收

- [ ] `uv run python scripts/verify_module.py`（適用範圍）
- [ ] **DB 實物**：查 `app_layer.workflow_outputs` 的 `job_result:ai:narrative`，
      確認 `query_audit`／`query_count`／`contract_warnings` 三欄實際存在（唯讀 SELECT）。
- [ ] **前端實物**：實機檢視 AI 任務卡，確認警告文字顯示；不接受「應該會顯示」。
- [ ] **實跑對比**：重跑 #452 的 `based_on_version = report_trial_20260820_094232`，
      與 #452 的產出對比：是否出現 MCP 取證呼叫、`evidence` 是否寫出、稽核三欄是否落庫。
      ⚠ #452 既有產出保留不動（決策 3）。
- [ ] **未驗項目揭露**：本 change 不涉 PPT 輸出，PPT 實物驗收不適用——須明確寫成
      「不適用」，不得以整體綠燈代替。
- [ ] `openspec validate --strict`

## 3. 收尾

- [ ] 在同一工作分支提交並推送。
- [ ] 建立 PR 作為合併紀錄；遠端 required checks 可用時須全綠，不可用時記錄原因並改以
      本機組合驗收證據與使用者允許作為閘門。
- [ ] 使用者明確接受組合驗收後才 archive，並同步 main specs 與 migration ledger。
- [ ] 把「其他 worktree 與主 repo 有同樣四個破口」記入待辦（proposal 的非阻塞 Open Question）。
