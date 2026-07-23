# OpenCode 正式版收斂六輪 Prompt

用途：案件比對先凍結，讓 OpenCode 依 TDD 分輪完成其餘產品主線。每輪做到驗收點就停止回報，不自行進下一輪。

## 進度銜接（2026-07-23 更新）

**第 1 輪已完成，不需重做——請直接從第 2 輪開始。**

第 1 輪「匯入到 workspace 主流程」已由主 session 完成並經獨立驗收 PASS 9/9（commit `6a15226`），
四項驗收點全數達成（實測：932 專利／20 workspace／19 個已掛專利）：

| 驗收點 | 現況 |
|---|---|
| fixture 檔案可匯入 | `POST /api/v1/imports` 支援 `filename`／`purpose`／`workspace_id`｜`new_workspace_name`（後兩者互斥→422） |
| DB 有 patents／workspace 關聯 | 成員存 `app_layer.workspaces.patent_ids_json`（去重 bigint 陣列） |
| `GET /workspaces` 可看到 workspace | 實測回 20 筆，支援 `purpose` filter |
| `GET /workspaces/{id}/patents` 可看到專利 | 實測 ws164 回 201 筆，每筆帶 `topic_key`／`topic_label` |

第 1 輪實際完成範圍**大於**原規劃（原規劃「purpose 只做 general」，實際 general／case_comparison 皆已支援）：

- 重匯更新政策：`update_patent_changed_fields` 逐欄「新值非空且與舊值不同才更新；新值空不覆蓋既有」（取代舊 COALESCE-only 只補 NULL）。
- `replace_people` 同步改為差異即更新（權利人／申請人可演進）。
- `import_wips_file` 回傳 `patent_ids`；handler `_attach_import_workspace` 支援新建／既有 workspace（union 去重、單一 transaction）。
- 用途標籤落 `app_layer.workspaces.settings_json.purpose`（不需新 DB 欄），`list_workspaces` 支援 `purpose` 過濾。

**第 2 輪開始前先讀**：`backend/app/app_layer/workspace_queries.py`、`workspace_create.py`、`backend/app/importers/wips_importer.py`，
了解既有匯入／workspace 實作，避免重複或衝突。

**第 4 輪 prompt 需注意**：`backend/app/api/companion.py` 正由主 session 改名為 `ai_tasks.py` 並加上 bearer token 認證
（原三端點零認證，服務已上公網屬安全缺口）。跑到第 4 輪時請先確認該檔實際檔名與路徑，prompt 內的 `companion.py`／
`tests/test_api_companion.py` 依當時實況替換。

**第 6 輪前端**：會大改 `backend/app/static/index.html`，與主 session 的前端改動須串行，開工前先確認無其他 agent 正在改該檔。

共通規則：
- 工作目錄：`D:\力山\專案\專利_ppt自動`。
- 一律採 TDD：先 Red、再 Green、最後必要 Refactor。
- 保留他人未提交變更，不 commit。
- 不碰案件比對：不得修改 `comparison`、`comparisons`、案件比對 API、案件比對 UI、案件比對測試。
- 不改 DB schema；若發現 schema 缺口，只列出缺口與建議 migration，不自行建立或套用 migration。
- 不清 DB、不操作正式資料。
- 不用假資料；API 與前端都以真後端資料為準。
- 每輪只跑該輪目標測試與必要回歸測試。
- 若同一問題連續兩輪修不出來，停止並回報阻塞點、已試方法、最小重現。

回報格式：
- Red 失敗原因
- Green 修改檔
- Refactor 調整
- 驗證指令與結果
- DB/schema 缺口
- 下一輪建議

---

## 第 1 輪：匯入到 Workspace 主流程 ✅ 已完成（2026-07-23，commit 6a15226，驗收 PASS 9/9）

> 本輪已由主 session 完成，範圍大於下方原規劃（含 case_comparison 用途與重匯差異更新政策）。
> 保留原 prompt 供追溯，**不需重跑**；細節見本檔開頭「進度銜接」。

```text
在 D:\力山\專案\專利_ppt自動 工作。採 TDD。案件比對先凍結，不得修改 comparison 相關檔案。不得改 DB schema；若發現 schema 缺口只列出，不建 migration。保留他人變更，不 commit。

目標：完成「匯入到 workspace 主流程」的正式 API/後端收尾。

先讀最小範圍：
- backend/app/api/imports.py 或現有 import API
- backend/app/imports/ 相關檔
- backend/app/clustering/workspace_service.py
- backend/app/api/workspaces.py
- tests 中 import/workspace 相關測試
- 目前 git diff

要求：
1. 先寫 Red 測試，覆蓋：用 fixture 匯入專利後，可建立或更新 workspace，且可用 API 查回 workspace patents。
2. Green 只做最小實作。
3. 匯入用途只做 general；case_comparison 暫時不碰。
4. 匯入成功後，workspace 與 patents 關聯可查。
5. 不用假資料，不清 DB，不碰 comparison。
6. 若現有 schema 不足，停止並列缺口，不自行 migration。
7. 完成後只跑 import/workspace 目標測試與必要回歸。
8. 驗收點達成後停止回報，不繼續下一輪。
```

驗收點：
- fixture 檔案可匯入。
- DB 有 patents / workspace 關聯。
- `GET /workspaces` 可看到 workspace。
- `GET /workspaces/{id}/patents` 可看到專利。

---

## 第 2 輪：分群主流程正式化 ✅ 已完成（2026-07-23，主 session 接手，驗收 PASS）

> OpenCode 該輪判定 FAIL（只做分析未實際 edit，`git diff` 為空），改由主 session 完成。
> 實際修了 **63 處** 0021 殘留（原估 36），範圍：`clustering/{runner,workspace_service,api}.py`＋`api/clustering.py`。
>
> **關鍵發現（推翻原假設）**：候選方案不能寫 `legacy_0021.topic_candidates`——該表 `run_id` FK 指向
> **凍結 archive** `legacy_0021.topic_runs`，新 run 寫入必 `ForeignKeyViolation`。
> 正確落點是 `derived_layer.topic_runs.topic_state_json->'candidates'`（讀寫同源已驗證）。
>
> 三類落點定案：
> | 類別 | 0021 落點 |
> |---|---|
> | 候選方案 candidates | `topic_state_json->'candidates'`（candidate_id run 內從 1 編號） |
> | 正式主題 topics | `topic_state_json->'topics'`（topic_id run 內遞增，topic_code 即 assignments 的 topic_key） |
> | 指派 assignments | `derived_layer.topic_assignments`（run_id, patent_id, topic_key） |
> | workspace_id／status | JOIN `app_layer.workflow_runs` 取得（topic_runs 已無這兩欄） |
>
> `workflow_run_id` 來源：job 本身即 workflow_run（`job_id == run_id`），handler 帶 `context.job.job_id`。
> 另發現 `app_layer.workspace_patents` 整表已刪（併入 `workspaces.patent_ids_json`）。
>
> **⚠ 遺留未動（已排為第 3 輪，見下）**：merge／unmerge／incremental 的合併鏈仍建在已刪的
> `derived_layer.topics` 上（serial `topic_id`、`merged_into_topic_id`、`FOR UPDATE` 列鎖），
> JSON 化後語意需重新設計，前一輪 agent 判斷不臆測改寫、留待另案。

<details>
<summary>原第 2 輪 prompt（保留追溯，不需重跑）</summary>

```text
在 D:\力山\專案\專利_ppt自動 工作。採 TDD。案件比對先凍結，不得修改 comparison 相關檔案。不得改 DB schema；若發現 schema 缺口只列出，不建 migration。保留他人變更，不 commit。

目標：完成 workspace 分群主流程正式化。

先讀最小範圍：
- backend/app/api/workspaces.py
- backend/app/api/topics.py 或現有 topic API
- backend/app/clustering/
- backend/app/worker/handlers.py
- backend/app/db/job_repository.py
- tests 中 clustering/topic/workspace/job 相關測試
- 目前 git diff

要求：
1. 先寫 Red 測試，覆蓋：workspace 可建立分群候選任務，worker 可產生候選方案，API 可查候選方案。
2. 候選主題數遵守既有規則：10 到 40，以 5 為間距；資料量分級取候選組，但前端最終要至少可呈現 3 組候選。
3. 使用者選定候選方案後，寫成正式 topic version。
4. API 可查正式 topics 與每個 topic 的 patents。
5. 不做 AI 標籤摘要，不做 topic merge/rename/unmerge。
6. 不用假資料，不清 DB，不碰 comparison。
7. 若 schema 不足，停止列缺口，不自行 migration。
8. 完成後只跑 clustering/topic/job 目標測試與必要回歸。
9. 驗收點達成後停止回報。
```

驗收點：
- workspace 可建立分群候選 job。
- 至少 3 組候選方案可查。
- 選定方案後有正式 topic version。
- job 狀態可從 queued/running 到 succeeded。

---

</details>

## 第 3 輪：Topic 人工操作 🔧 主 session sub agent 執行中（2026-07-23 改派）

> **改派原因**：第 2 輪修完 0021 落點後發現，merge／unmerge／incremental 的整條合併鏈仍建在
> **已刪除的 `derived_layer.topics` 表**上，且原設計依賴 serial `topic_id`、`merged_into_topic_id`
> 外鍵與 `FOR UPDATE` 列鎖——JSON 化（`topic_state_json`）後這些語意（尤其**併發保護**）需重新設計。
> 此輪難度與風險高於原 prompt 預期（原以為只是驗證既有端點），改由主 session sub agent 處理。
>
> 現況：後端 5 個端點（merge-suggestions／merge／merge-history／unmerge／PATCH rename）路由存在，
> 前端 UI 已完成（分類區內），但**後端執行路徑斷**——按下去會失敗。
>
> OpenCode **不需執行本輪**。

<details>
<summary>原第 3 輪 prompt（保留追溯）</summary>

```text
在 D:\力山\專案\專利_ppt自動 工作。採 TDD。案件比對先凍結，不得修改 comparison 相關檔案。不得改 DB schema；若發現 schema 缺口只列出，不建 migration。保留他人變更，不 commit。

目標：完成正式 topic version 的人工操作 API。

先讀最小範圍：
- backend/app/api/topics.py
- backend/app/clustering/workspace_service.py
- backend/app/clustering/topic 相關服務
- backend/app/db/topic 相關 repository
- tests/test_api_topics_contract.py
- topic merge/unmerge/rename 相關測試
- 目前 git diff

要求：
1. 先寫 Red 測試，覆蓋 merge、rename、unmerge。
2. merge：只能操作正式 topic version，不操作候選方案。
3. rename：保留 manual label，不能被 AI label 覆蓋，除非後續明確重新命名。
4. unmerge：只依 merge history 還原，不做任意 split。
5. 未分類 topic 保留為「未分類」，讓使用者後續決定，不自動丟棄。
6. 第一次分群後不提供任意 split 功能。
7. 不用假資料，不清 DB，不碰 comparison。
8. 若 schema 不足，停止列缺口，不自行 migration。
9. 完成後只跑 topic API 目標測試與必要回歸。
10. 驗收點達成後停止回報。
```

驗收點：
- merge 後 patent count 正確合併。
- rename 後 label 正確保存。
- unmerge 可回到合併前狀態。
- 沒有任意 split 入口。

---

</details>

## 第 4 輪：AI 標籤與摘要 ⬅️ **OpenCode 下一輪做這個**（2026-07-23）

> **⚠ 原 prompt 有過時檔名，以此處更正為準**：
> | 原文 | 現況 |
> |---|---|
> | ~~`backend/app/api/companion.py`~~ | 已改名 **`backend/app/api/ai_tasks.py`**，並加 bearer token 認證（依賴 `backend/app/api/_auth.py` 的 `require_api_token`） |
> | ~~`tests/test_api_companion.py`~~ | 已改名 **`tests/test_api_ai_tasks.py`** |
> | 「Companion」語意 | `backend/app/worker/ai_bridge.py` 才是 host-side AI 橋接器（claim AI job→驅動本機 CLI→回寫 workflow_outputs），已驗證可 run（doctor／smoke PASS）。`api/ai_tasks.py` 是「Web 前端建 AI 任務的入口」，**不是**裝置側取任務通道 |
>
> **本輪禁區**（主 session 未 commit 的改動，需要動到就停下回報）：
> - `backend/app/clustering/**`（第 2 輪 0021 修正 + 第 3 輪 merge 重設計中）
> - `backend/app/static/index.html`（四塊 UI ＋ 匯出報告工作台）
> - `backend/app/main.py`（新增 report-latest/content 端點）
> - `backend/app/api/ai_tasks.py`、`backend/app/api/_auth.py`
>
> **可動範圍**：`backend/app/worker/ai_bridge.py`、`backend/app/api/topics.py`、
> `backend/app/db/job_repository.py`、clustering 的 topic 代表性文檔查詢（唯讀 import）及對應測試。
>
> **⚠ 前兩輪教訓**：務必實際執行 edit，不要停在分析階段；回報前自行 `git diff --stat` 並貼進回報作為憑證。
>
> **本輪價值**：分群完的主題名目前是關鍵詞拼接（如 `unit / said / second`），
> 需 AI 標籤才會變成人看得懂的中文主題名——這是使用者實際會看到的東西。

```text
在 D:\力山\專案\專利_ppt自動 工作。採 TDD。案件比對先凍結，不得修改 comparison 相關檔案。不得改 DB schema；若發現 schema 缺口只列出，不建 migration。保留他人變更，不 commit。

目標：完成正式 topic 的 AI 標籤與摘要任務，透過 Companion 建立 ai:narrative job。

先讀最小範圍：
- backend/app/api/companion.py
- backend/app/worker/ai_bridge.py 或 AI bridge 相關檔
- backend/app/db/job_repository.py
- backend/app/api/topics.py
- backend/app/clustering/topic 代表性文檔查詢相關檔
- tests/test_api_companion.py
- tests/test_ai_bridge.py
- tests 中 topic representative docs 相關測試
- 目前 git diff

要求：
1. 先寫 Red 測試，覆蓋：針對正式 topic version 建立 AI 標籤/摘要任務。
2. 每個 topic 取 topic probability 最高前 5 筆代表性專利。
3. payload 給 LLM 的內容只包含文檔與必要 metadata，不給 c-TF-IDF keywords。
4. 建立 `ai:narrative` job，結果寫入 `app_layer.workflow_outputs`。
5. Companion task API 可查回狀態與結果。
6. 一般 worker 不消費 AI job；AI bridge 是獨立執行路徑。
7. 不真的呼叫外部 Claude CLI；真 CLI E2E 另做 smoke。本輪用測試替身驗契約。
8. 不用假資料，不清 DB，不碰 comparison。
9. 若 schema 不足，停止列缺口，不自行 migration。
10. 完成後只跑 companion/ai_bridge/topic 目標測試與必要回歸。
11. 驗收點達成後停止回報。
```

驗收點：
- 可建立 AI 標籤/摘要任務。
- request payload 有 workspace、topic version、topic、前 5 筆代表性專利。
- 結果可由 Companion task API 查回。
- 一般 worker 不吃 AI job。

---

## 第 5 輪：報表引擎正式收尾 ✅ 已完成（2026-07-23，OpenCode，驗收 PASS 8/8）

> 交付：`GET /api/v1/report-definitions`（動態從 `REPORT_DEFINITIONS` 產生報表目錄，14 筆，無寫死）
> ＋ `tests/test_report_analysis_types.py`（六大分析類型輸出契約）。
> 驗收點 a／b／d（多版本不覆蓋、查詢回存、filters 篩選）確認為**既有實作**，本輪實質新增為驗收點 c。
> 瑕疵：work-log 記「109 passed」與實測「86 passed／20 skipped」不符（DB 測試需 `RUN_DB_TESTS=1`）。

<details>
<summary>原第 5 輪 prompt（保留追溯，不需重跑）</summary>

```text
在 D:\力山\專案\專利_ppt自動 工作。採 TDD。案件比對先凍結，不得修改 comparison 相關檔案。不得改 DB schema；若發現 schema 缺口只列出，不建 migration。保留他人變更，不 commit。

目標：報表引擎正式收尾，讓前端與 PPT Skill 可使用結構化報表結果。

先讀最小範圍：
- backend/app/reports/report_definitions.py
- backend/app/reports/report_engine.py
- backend/app/reports/chart_runner.py
- backend/app/reports/cluster_analytics.py
- backend/app/api/reports.py
- backend/app/mcp_server/tools_reporting.py
- tests 中 report/chart/MCP 相關測試
- 目前 report 相關 git diff

要求：
1. 先寫 Red 測試，覆蓋本輪缺口。
2. 報表結果以 DB structured output / JSON 為主，不以 CSV 作正式資料來源。
3. 完成或確認以下正式報表：
   - 申請人分析
   - 專利權人分析
   - 公司 × 國家矩陣
   - 主題 × 公司分布
   - 專利密度 × 競爭者結構強度
   - 痛點 × 專利訊號
4. 全庫與 workspace 走同一 report definition；workspace 只增加 patent_ids snapshot 限制，統計公式不可分叉。
5. 同 workspace 重跑不得覆蓋舊結果，必須保留 version/run。
6. 報表結果要能提供給前端與 PPT Skill。
7. Market evidence 只做既有資料引用與輸出契約；外部搜尋流程另輪處理。
8. 不用假資料，不清 DB，不碰 comparison。
9. 若 schema 不足，停止列缺口，不自行 migration。
10. 完成後只跑 report/chart/MCP 目標測試與必要回歸。
11. 驗收點達成後停止回報。
```

驗收點：
- 同一 workspace 可產生完整報表結果。
- report version 不覆蓋舊結果。
- API/MCP 可查 structured output。
- filters / workspace / patent snapshot 可追蹤。

---

</details>

## 第 6 輪：前端真 API 接線與容器化準備 🟡 主 session 已完成大半（2026-07-23）

> 已由主 session 完成（未 commit 或剛 push）：
> - 四區骨架接真 API（workspace／專利總覽／分類區／AI 助手）
> - 匯入確認流程（摘要→確認→進度→結果卡→前往 workspace）
> - 分群任務區（calibrate／incremental／候選表／finalize）
> - topic 人工操作 UI（重命名／合併建議／合併／歷史／unmerge）— **後端待第 3 輪修好才真能跑**
> - AI 任務結果顯示（原本抓了丟棄）＋ AI 任務金鑰欄（token 存 localStorage、Bearer 標頭）
> - **匯出報告工作台**：完整預覽＋編輯模式（AI 解讀文案／封面資訊／自由段落）＋匯出自包含單頁 HTML（含 `@media print` 可列印 PDF）
> - 後端補洞：`GET /api/v1/report-latest/content`（結構化內容）、`/asset/{version}/{filename}`（圖檔，白名單＋path traversal 防護）
>
> **剩餘未做**（可作 OpenCode 後續輪或另案）：
> - 市場佐證 UI（market 5 條端點全無 UI）
> - workspace 建立／組合入口（`POST /workspaces`、`/workspaces/compose`）
> - `GET /ai-tasks/status` 能力顯示
> - 前端容器化（獨立 `frontend/` ＋ Dockerfile／nginx；目前前端在 backend image 內由 FastAPI serve）
> - API base URL runtime config（目前 `const API = '/api/v1'` 同源，跨網域部署時需可切換）

<details>
<summary>原第 6 輪 prompt（保留追溯）</summary>

```text
在 D:\力山\專案\專利_ppt自動 工作。採 TDD。案件比對先凍結，不得修改 comparison 相關檔案或新增案件比對功能。不得改 DB schema；若發現 schema 缺口只列出，不建 migration。保留他人變更，不 commit。

目標：把 Claude 先做過的臨時前端收斂成可驗收、真 API、可容器化的前端雛形。

先讀最小範圍：
- backend/app/static/index.html
- backend/app/api/workspaces.py
- backend/app/api/topics.py
- backend/app/api/imports.py
- backend/app/api/reports.py
- backend/app/api/companion.py
- tests/test_api_frontend.py
- docs/frontend_interface_plan.md
- 目前 frontend/static 相關 git diff

要求：
1. 先寫 Red 測試，覆蓋前端文字可讀、API endpoint 存在、核心操作入口存在。
2. 修正現有 `backend/app/static/index.html` mojibake，所有使用者可見文字改成可讀繁中。
3. 保留既有真 API 接線，不退回假資料。
4. 補齊主流程 UI：
   - 匯入成功後導向 workspace / 分群
   - 分群候選方案列表
   - 選定候選方案
   - 正式 topic 顯示
   - topic merge / rename / unmerge
   - Companion AI 標籤摘要任務與結果顯示
   - 報表產製、report version、結果入口、PPT 入口
5. 報表清單不要硬寫死；若後端已有 catalog API 就使用，若沒有就列缺口，不自行改 schema。
6. 錯誤處理不要只用 alert；主要流程錯誤要能在頁面內顯示可讀訊息。
7. 案件比對 UI 暫時不新增功能、不修主流程；可保留或隱藏，但不納入驗收。
8. 前端最終需要容器化。請在不重寫成大型框架的前提下，將現有臨時前端整理成可容器化前端雛形。
9. API base URL 不得寫死，需可由 runtime config 或環境設定切換。
10. 若需要新增 `frontend/` 目錄、`frontend/Dockerfile`、`nginx.conf` 或靜態 server 設定，可以做，但不要影響 backend/worker 啟動。
11. 不用假資料，不清 DB，不碰 comparison。
12. 完成後只跑 frontend/API 目標測試與必要回歸。
13. 驗收點達成後停止回報。
```

驗收點：
- 前端無亂碼。
- 可用真 API 走：匯入/選 workspace → 分群候選 → 選方案 → topic 操作 → AI 摘要 → 報表產出。
- API 錯誤在頁面內可讀顯示。
- 前端具備容器化雛形。
- API base URL 可切換，不寫死。
- 不碰案件比對。

</details>
