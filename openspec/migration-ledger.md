# OpenSpec 導入與舊規格遷移清冊

本檔只記錄來源遷移與清理，不保存功能規格正文。功能現況以 `openspec/specs/` 為準，未完成變更以 `openspec/changes/` 為準。

## 狀態定義

- `baseline`：已由現行程式、測試與輸出契約重建為 main spec。
- `active-change`：仍在規劃、實作或驗收，已移入 OpenSpec change。
- `runbook`：保留在 `docs/`，內容是可執行操作而非功能規格。
- `reference`：保留作外部參考或設計研究，不代表現行功能。
- `retired`：已作廢或被取代；承接資訊確認後移除舊檔。
- `historical`：一次性交接或舊快照；穩定資訊已由 git、work-log、decision 或 OpenSpec 承接後移除。

## Baseline 能力

| Capability | 現行規格 | 主要程式證據 |
|---|---|---|
| 平台與工作佇列 | `specs/platform-runtime/` | `backend/app/main.py`、`backend/app/db/job_repository.py`、`backend/app/worker/` |
| 專利匯入 | `specs/patent-ingestion/` | `backend/app/importers/`、`backend/app/mappings/`、`backend/app/api/imports.py` |
| 專利資料模型 | `specs/patent-data-model/` | `alembic/versions/`、`backend/app/derived/`、`backend/app/app_layer/` |
| Workspace 與瀏覽 | `specs/workspace-and-browse/` | `backend/app/api/workspaces.py`、`backend/app/api/patents.py` |
| 公司治理 | `specs/company-governance/` | `backend/app/api/company_aliases.py`、`backend/app/derived/company_alias_importer.py` |
| 分群與主題治理 | `specs/clustering-and-topics/` | `backend/app/clustering/`、`backend/app/repositories/postgres_topic_repository.py` |
| AI Companion | `specs/ai-companion/` | `backend/app/worker/ai_bridge.py`、`backend/app/worker/ai_*_runner.py` |
| 報表 | `specs/patent-reporting/` | `backend/app/reports/`、`backend/app/api/reports.py` |
| PPT 匯出 | `specs/report-export/` | `skills/patent-report-ppt/`、報表版本與 artifact API |
| 案件比對 | `specs/patent-comparison/` | `backend/app/comparison/`、`backend/app/api/comparison.py` |

## 遷移與清理

沒有承接位置的檔案不得刪除。`legacy source` 不是權威規格，只保存尚未逐條吸收的細部決策；對應 change archive 前必須完成差異核對。

| 舊來源 | 分類 | 目標／理由 | 處置 |
|---|---|---|---|
| `docs/frontend_snapshot_cache_spec.md` | active-change | `changes/add-frontend-snapshot-cache/` | 保留為 legacy source；區塊級更新、人工編輯落 DB 等細節尚待吸收 |
| `docs/patent_core_field_reclassification_spec.md` | active-change | `changes/complete-core-field-reclassification/` | 保留為 legacy source；A5／完整 DB 驗收前不得刪除 |
| `docs/retention_archive_plan.md` | active-change | `changes/implement-retention-archive/` | 保留為 legacy source；volume、NAS 與還原細節尚待吸收 |
| `D:/力山/.agents/context/report-professionalism-spec.md` | active-change | `changes/improve-report-professionalism/`、`changes/enable-goal-driven-readonly-report-planning/` | 保留母體、雙通道與內容品質決策；固定頁序／固定 Key Player 三頁已由 goal-driven 規劃取代 |
| `D:/力山/.agents/context/clustering-dpmeans-spec.md` | active-change | `changes/replace-clustering-with-dpmeans/` | 保留為 legacy source，archive 前逐條核對 |
| `D:/力山/.agents/context/import-format-fixes-spec.md` | active-change | `changes/harden-import-formats/` | 保留為 legacy source，archive 前逐條核對 |
| `D:/力山/.agents/context/installer-spec.md` | active-change | `changes/package-patent-toolkit-installer/` | 保留為 legacy source，archive 前逐條核對 |
| `D:/力山/.agents/context/sse-auto-refresh-spec.md` | active-change | `changes/complete-sse-data-refresh/` | 保留為 legacy source，archive 前逐條核對 |
| `D:/力山/.agents/context/object-storage-plan.md` | active-change | `changes/move-import-uploads-to-object-storage/` | 保留為 legacy source；供應商選型、既有 blob 遷移與切換閘門尚待實作時逐條核對 |
| `D:/力山/.agents/context/export-report-flow-spec.md` | active-change | `changes/enable-goal-driven-readonly-report-planning/`、`changes/complete-export-report-editing/` | 初次產出改由最大目標＋選圖＋唯讀證據規劃；歷史、草稿與單頁候選由 editing change 承接 |
| `D:/力山/.agents/context/e2e-test-checklist-three-zones.md` | active-change | `changes/complete-three-zone-e2e-acceptance/` | 保留為實機 runbook／legacy source；固定舊環境版次不得視為現行契約 |
| `D:/力山/.agents/context/known-issues-optimization.md` D2/D3 | active-change | `changes/harden-runtime-security-and-configuration/` | AI 認證、DB fail-fast、readiness 與 Companion 降級告警由 change 承接 |
| `D:/力山/.agents/context/todo-tracker.md` C14-C16 | active-change | `changes/establish-quality-automation/` | Python 版本、lint/type/CI、跨層契約檢查由 change 承接 |
| `D:/力山/.agents/context/todo-tracker.md` C10 | active-change | `changes/add-batch-exclusion-review/` | 待複核批次選取、提交與分群版本一致性由 change 承接 |
| 2026-08-06 goal-driven 報告決策 | active-change | `changes/enable-goal-driven-readonly-report-planning/` | 無獨立舊規格檔；最大目標、全部使用者選圖、唯讀補證據與動態 SlidePlan 直接由 OpenSpec 承接 |
| `D:/力山/.agents/context/frontend-ux-fixes.md` | active-change | `specs/workspace-and-browse/`、`specs/platform-runtime/`、`changes/complete-three-zone-e2e-acceptance/`、`changes/complete-export-report-editing/` | UX-1～4 已由 baseline／E2E 承接；解讀切換與單張重產由 editing change 承接，舊檔不再作待辦來源 |
| `D:/力山/.agents/context/ppt-space-theme-plan.md` §23 | active-change | `changes/show-honest-progress-for-long-ai-tasks/` | 不可量測 CLI 階段的 indeterminate＋elapsed time 已建立完整 proposal/spec/design/tasks |
| `D:/力山/.agents/context/ppt-space-theme-plan.md` §25 | active-change | `changes/separate-web-and-ppt-chart-profiles/` | web／PPT 同 identity 雙 profile、選圖完整傳 CLI 與 fail-loud 閘門已建立完整 artifacts |
| `D:/力山/.agents/context/ppt-space-theme-plan.md` 其餘活項 | active-change | `changes/improve-report-professionalism/`、`changes/complete-export-report-editing/`、`changes/enable-goal-driven-readonly-report-planning/` | 內容、編輯與動態頁序分流承接；W-3／大量主題仍依下方「明確延後」管理 |
| `D:/力山/.agents/context/ppt-visual-rework-spec.md` | active-change | `specs/report-export/`、`changes/improve-report-professionalism/`、`changes/complete-export-report-editing/` | 已落地版型進 baseline；尚未完成的真實 PPT 編輯／內容品質由 changes 承接 |
| `D:/力山/.agents/context/ppt-skill-creator-prompt.md` | reference | `specs/report-export/`、`changes/enable-goal-driven-readonly-report-planning/`、`changes/separate-web-and-ppt-chart-profiles/` | 保留為 skill 重建歷史輸入，不再作現行需求來源 |
| `D:/力山/.agents/context/report-requirements.md` | reference | `specs/patent-reporting/`、`specs/report-export/` 與現行 report changes | 穩定報表契約已吸收；舊 artifact／頁序口徑只供歷史追溯，不得覆蓋 OpenSpec |
| `D:/力山/.agents/context/report-catalog-plan.md` | retired | `specs/patent-reporting/`、`changes/improve-report-professionalism/`、`changes/complete-export-report-editing/` | 舊 14/15 種報表與固定新增路線已被後續規劃取代；有效互動缺口已承接 |
| `D:/力山/.agents/context/patent-db-claude-plan.md` | active-change | `specs/patent-data-model/`、`specs/patent-ingestion/`、`changes/complete-core-field-reclassification/` | 功能契約與 A5 任務由 OpenSpec 承接；本檔只保留實際 DB head、部署與驗收證據 |
| `D:/力山/.agents/context/comparison-phase2-parked.md` | reference | `specs/patent-comparison/` | baseline 只描述保留的後端能力與未完成輸出護欄；第二階段凍結，不建立 active change，重啟時另提案 |
| `D:/力山/.agents/context/market-doc-summary-spec.md` | retired | 0044 已移除市場線；歷史決策由 git 與 `decisions.md` 保存 | 已記錄，移除 |
| `D:/力山/.agents/context/market-research-implementation.md` | retired | 0044 已移除市場線；不再作工程規格 | 已記錄，移除 |
| `D:/力山/.agents/skills/market-data-flow.md` | retired | 市場線已由 0044 移除，runbook 無可執行產品路徑 | 已記錄，移除 |
| `docs/handoff/*.md` | historical | 一次性交接，現況已由程式、git、OpenSpec 與中央 context 取代 | 已記錄，移除 |
| `docs/database_schema_v1.md` | retired | 早期 7 表草案已被現行 migrations 與 `specs/patent-data-model/` 取代 | 已記錄，移除 |
| `docs/app_layer_design.md` | retired | 早期 app layer 草案已被現行 workflow/repository 與 baseline specs 取代 | 已記錄，移除 |
| `docs/work_summary.md` | historical | 2026-07-02 一次性快照，現況由 git、work-log 與 OpenSpec 取代 | 已記錄，移除 |
| `D:/力山/.agents/context/ai-job-guard-spec.md` | baseline | `specs/ai-companion/`、`specs/platform-runtime/` | 已結案；待 router 引用清理後移除 |
| `D:/力山/.agents/context/applicant-code-grouping-spec.md` | baseline | `specs/company-governance/` | 已結案；細部實機證據仍由 context 保存，暫不刪 |
| `D:/力山/.agents/context/company-zh-name-confirm-spec.md` | baseline | `specs/company-governance/`、`specs/ai-companion/` | 已結案；細部 API 決策尚待吸收，暫不刪 |
| `D:/力山/.agents/context/normalization-doc-cleanup-spec.md` | baseline | `specs/company-governance/` | 已結案；待 router 引用清理後移除 |
| `D:/力山/.agents/context/report-layout-tabs-spec.md` | baseline | `specs/patent-reporting/`、`specs/report-export/` | 已結案；細部 UI 驗收尚待吸收，暫不刪 |
| `D:/力山/.agents/context/irrelevant-patent-filter-spec.md` | baseline | `specs/clustering-and-topics/`、`specs/workspace-and-browse/` | pending／保留／確認／復原、全庫護欄與不刪核心資料已吸收；舊檔只留決策沿革 |
| `D:/力山/.agents/context/patent-display-spec.md` | baseline | `specs/workspace-and-browse/` | 共用欄位、正規化／原文、缺值、連結、批次投影與圖片延遲載入已吸收；舊檔只留欄位沿革 |
| `D:/力山/.agents/context/patent-figures-design.md` | baseline | `specs/patent-ingestion/`、`specs/workspace-and-browse/` | migration 0031 的 paired figures、代表圖優先序與冪等匯入已吸收；舊檔只留設計背景 |
| `D:/力山/.agents/context/topic-api-contract.md` | baseline | `specs/clustering-and-topics/` | stable topic_key、六組 API、Repository Protocol／DI 與正式 PostgreSQL adapter 已吸收；舊檔只留細部錯誤碼沿革 |
| `D:/力山/.agents/context/rtm-2026-07-26-three-items.md` | baseline | `specs/workspace-and-browse/`、`specs/platform-runtime/`、`specs/report-export/` | #2、#7、#10 已由程式與測試確認並吸收；矩陣保留為歷史驗收證據，不再是現行規格 |

## Context 與歷史來源覆蓋

下列文件經盤點不應轉成 Requirement；其功能性內容已由上方 baseline／active change 承接，原檔只保留指定用途。

| 來源 | 分類 | 保留用途／承接位置 |
|---|---|---|
| `D:/力山/.agents/context/backend-worker-to-lightning-steps.md` | runbook | Lightning 部署歷史步驟；現行功能契約見 `specs/platform-runtime/`，實際部署先核對 `docs/lightning_ai_deployment.md` |
| `D:/力山/.agents/context/codex-thread-coordinator.md` | reference | 開發工具環境事實，非專利產品能力 |
| `D:/力山/.agents/context/data-sources.md` | reference | GPSS/WIPS 外部來源背景；匯入契約見 `specs/patent-ingestion/` |
| `D:/力山/.agents/context/db-to-railway-steps.md` | retired | Railway DB 容量失敗歷史，不是現行部署方案 |
| `D:/力山/.agents/context/db-to-supabase-steps.md` | runbook | Supabase 連線與 pooler 操作；runtime 契約見 `specs/platform-runtime/` |
| `D:/力山/.agents/context/local-model-and-mcp.md` | reference | 本地模型／MCP 環境研究，非本產品規格 |
| `D:/力山/.agents/context/obsidian-mcp.md` | reference | Obsidian 工具環境，與專利產品規格無關 |
| `D:/力山/.agents/context/patent-backend-claude-plan.md` | historical | Claude 執行／交接快照；現行 API 與 repository 契約見 OpenSpec |
| `D:/力山/.agents/context/patent-backend-worker-plan.md` | historical | 早期 processing_jobs／container 驗收快照；現行 queue 契約見 `specs/platform-runtime/` |
| `D:/力山/.agents/context/patent-intelligence-workflow.md` | historical | 逐日架構演進紀錄；現行產品流程看 `.ai-rules/workflows.md` 與 OpenSpec |
| `D:/力山/.agents/context/patent-mcp-clustering-status.md` | historical | MCP／分群舊狀態；現行契約見 `specs/ai-companion/`、`specs/clustering-and-topics/`、`specs/patent-reporting/` |
| `D:/力山/.agents/context/patent-taxonomy-design.md` | historical | BERTopic 舊設計；現行 baseline 與 DP-Means 變更見 `specs/clustering-and-topics/`、`changes/replace-clustering-with-dpmeans/` |
| `D:/力山/.agents/context/project-background.md` | reference | 工作區與 CLI-first 背景，不是功能契約 |
| `D:/力山/.agents/context/README.md` | governance | 全域 context/router，本身不轉功能 spec |

## Repo 文件覆蓋

| 來源 | 分類 | 保留用途／承接位置 |
|---|---|---|
| `AGENTS.md`、`CLAUDE.md` | governance | Agent 入口與專案工作規則，不轉產品 spec |
| `alembic/README.md` | runbook | Alembic 操作說明；schema 契約見 `specs/patent-data-model/` |
| `archive/deprecated/**` | historical | 已封存實作與說明，不得回流現行需求 |
| `archive/taxonomy-v0/**` | historical | taxonomy v0 歷史 POC，不得覆蓋現行 clustering spec/change |
| `docs/assignee_normalization_workflow.md` | runbook | 公司正規化操作；功能契約見 `specs/company-governance/` |
| `docs/backend_worker_smoke_checklist.md` | runbook | backend/worker 冒煙操作；契約見 `specs/platform-runtime/` |
| `docs/clustering_engine_design.md` | reference | 引擎架構背景；契約見 `specs/clustering-and-topics/` 與 DP-Means change |
| `docs/database_operations.md` | runbook | DB 操作，不是功能規格 |
| `docs/docker_workflow.md` | runbook | 容器操作，不是功能規格 |
| `docs/frontend_interface_plan.md` | historical | 早期介面規劃；現行 UI 契約見 workspace/report/export specs 與 changes |
| `docs/frontend_snapshot_cache_spec.md` | active-change | `changes/add-frontend-snapshot-cache/` |
| `docs/import_rules.md` | runbook | 匯入操作／格式參考；契約見 `specs/patent-ingestion/` |
| `docs/infringement_comparison_design.md` | reference | 凍結案件比對設計；現行已實作後端邊界見 `specs/patent-comparison/` |
| `docs/lightning_ai_deployment.md` | runbook | Lightning 部署操作；runtime 契約見 `specs/platform-runtime/` |
| `docs/patent_core_field_reclassification_spec.md` | active-change | `changes/complete-core-field-reclassification/` |
| `docs/patent_tool_architecture_summary.md` | reference | 架構總覽；各能力契約見對應 baseline specs |
| `docs/ppt_skill_input_contract.md` | reference | 現行輸入契約由 `specs/report-export/` 與 goal-driven/chart-profile changes 承接 |
| `docs/report_field_matrix.md` | reference | 報表欄位追溯；功能契約見 `specs/patent-reporting/` |
| `docs/retention_archive_plan.md` | active-change | `changes/implement-retention-archive/` |
| `docs/T-352-zone-classification.md` | reference | 外部分類研究，不直接形成產品 Requirement |
| `skills/patent-report-ppt/*.md` | runbook | 產品內可執行 skill 與內容規則；行為契約仍以 `specs/report-export/` 與 active changes 為準 |

## 盤點後已由現況承接

下列舊待辦經 2026-08-06 對照程式、migration 與測試後，不再建立 active change：

| 舊待辦 | 現行承接位置 | 結論 |
|---|---|---|
| 報表版本保存 `topic_run_id`／`topic_state_version` | `specs/patent-reporting/`、`specs/report-export/` | 已在 chart runner 與 PPT 輸出鏈使用 |
| 專利圖片中期版 | `specs/patent-ingestion/`、`specs/workspace-and-browse/` | migration 0031、批次寫入與 paired tests 已完成 |
| MCP HTTP 啟動 | `specs/platform-runtime/` | streamable HTTP 與 bearer token 已完成 |
| 上傳進度與早期瀏覽 UX | `specs/patent-ingestion/`、`specs/workspace-and-browse/` | XHR progress、workspace、橫向捲動與匯入摘要已有程式／測試 |
| import blob 終結態清理與孤兒掃描 | `specs/platform-runtime/` | 現有清理保留；object-storage change 只承接大檔不經 DB 的剩餘目標 |

## 明確延後或等待產品決策

這些項目目前不得交給實作者自行補需求；決策成熟後另開 OpenSpec change：

| 項目 | 原因 |
|---|---|
| 30／40+ 大量主題呈現 | 象限板取捨與內頁排序尚未定案 |
| W-3 插圖 | 尚無內容、版位與驗收規格 |
| 案件比對第二階段 | 使用者已凍結 |
| Railway DB | 現有額度／Volume 條件不可行 |
| index 四張未知卡 | 尚待使用者指認實際 UI |
| 大量匯入 COPY 最佳化 | 尚無可重現 benchmark、資料量與目標門檻 |

## Archive 紀錄

| change | 結果 | 日期 |
|---|---|---|
| `complete-core-field-reclassification` | ✅ 使用者驗收通過後 archive；spec 增量（DAT-006／RPT-008／EXP-007）已併入主規格；隔離庫 migcheck_0046 已依裁決刪除 | 2026-08-06 |
| `complete-sse-data-refresh` | ✅ 使用者驗收通過後 archive；PRT-005 改寫（成功終結刷新 mapping＋失敗不刷資料）與 WSP-007 新增已併入主規格；三根因（6543 pooling、notifies 壽命、前端不重連）修復合 master `970406c` | 2026-08-12 |
| `unify-chart-source` | ✅ 使用者驗收通過後 archive（標的 report_trial_20260812_133901）；RPT-010 圖表單一來源已併入主規格；含驗收期修正（年度矩陣交叉表、術語主題化、泡泡列向守門、檢視選單五變體、版本下拉） | 2026-08-12 |
