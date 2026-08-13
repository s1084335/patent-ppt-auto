# AI Companion Design

## 架構與資料流

Backend 只建立 AI job；Windows Companion 輪詢同一 PostgreSQL queue，依 registry 延遲載入 runner，呼叫本機 AI CLI，再透過 repository/API 寫回。現役 AI 工作共八條：narrative、topic backfill、topic label、patent note、candidate explanation、company zh name、company group suggestion 與 irrelevant filter。已退役的 report plan／report PPT 不得出現在 job registry。

大 payload 經 `ai_payload_file.py` 落檔；CLI JSON 解析集中處理，容忍 code fence 或少量前後贅字，但缺少必要 JSON 不可猜測。集團建議也使用同一 JSON 抽取器，再執行候選身分、HTTPS 證據與 suggested-only 的 domain 驗證。跨容器產物必須進 DB-backed artifact store。

`cli_gateway.py` 是 CLI argv、執行器、結果 envelope 與工具權限的唯一共用落點。四級權限為 `NO_TOOLS`、`READ_ONLY_TOOLS`、`RESEARCH_TOOLS`、`WEB_RESEARCH_TOOLS`；runner 不得從 narrative 或其他任務模組 re-export 共用符號。守門測試以 `AI_JOB_TYPES` 為全集，並檢查各 job 真正使用的 argv 路徑。

## 程式落點

- Bridge：`backend/app/worker/ai_bridge.py`、`ai_bridge_serve.py`
- Payload：`backend/app/worker/ai_payload_file.py`
- CLI gateway：`backend/app/worker/cli_gateway.py`
- Runners：`backend/app/worker/ai_*_runner.py`
- API：`backend/app/api/ai_tasks.py`、`backend/app/api/events.py`
- 共用 job types：`backend/app/db/job_repository.py`

## 測試證據

- `tests/test_ai_bridge.py`、`tests/test_ai_bridge_serve.py`
- `tests/test_ai_job_registration_guard.py`
- `tests/test_ai_payload_file.py`
- `tests/test_ai_runners_use_payload_file.py`
- `tests/test_cli_json_extraction.py`
- `tests/test_cli_gateway.py`
- 各 runner：`tests/test_ai_narrative_runner.py`、`test_topic_backfill_runner.py`、`test_ai_topic_label.py`、`test_ai_patent_note.py`、`test_ai_candidate_explanation.py`、`test_ai_company_zh_name.py`、`test_ai_company_group_suggestion.py`、`test_ai_irrelevant_filter.py`
- `tests/test_api_ai_tasks.py`

## 輸出契約

每個 runner 回傳結構化摘要並寫入任務專屬的 DB 或 artifact。工作成功必須代表消費端可重新讀取結果，而非只代表 subprocess exit code 為零。

## 部署限制

Companion 使用使用者本機 CLI 登入態，不進 backend/worker container。修改常駐 Companion 程式後需重啟進程；正式驗收要比對進程啟動時間與部署程式版本。

