# AI Companion Design

## 架構與資料流

Backend 只建立 AI job；Windows Companion 輪詢同一 PostgreSQL queue，依 registry 延遲載入 runner，呼叫本機 AI CLI，再透過 repository/API 寫回。AI 工作目前包括 narrative、topic label、patent note、candidate explanation、company zh name、irrelevant filter 與 report PPT。

大 payload 經 `ai_payload_file.py` 落檔；CLI JSON 解析集中處理，容忍 code fence 或少量前後贅字，但缺少必要 JSON 不可猜測。跨容器產物必須進 DB-backed artifact store。

## 程式落點

- Bridge：`backend/app/worker/ai_bridge.py`、`ai_bridge_serve.py`
- Payload：`backend/app/worker/ai_payload_file.py`
- Runners：`backend/app/worker/ai_*_runner.py`
- API：`backend/app/api/ai_tasks.py`、`backend/app/api/events.py`
- 共用 job types：`backend/app/db/job_repository.py`

## 測試證據

- `tests/test_ai_bridge.py`、`tests/test_ai_bridge_serve.py`
- `tests/test_ai_job_registration_guard.py`
- `tests/test_ai_payload_file.py`
- `tests/test_ai_runners_use_payload_file.py`
- `tests/test_cli_json_extraction.py`
- 各 runner：`tests/test_ai_narrative_runner.py`、`test_ai_topic_label.py`、`test_ai_patent_note.py`、`test_ai_company_zh_name.py`、`test_ai_irrelevant_filter.py`、`test_ai_report_ppt.py`
- `tests/test_api_ai_tasks.py`

## 輸出契約

每個 runner 回傳結構化摘要並寫入任務專屬的 DB 或 artifact。工作成功必須代表消費端可重新讀取結果，而非只代表 subprocess exit code 為零。

## 部署限制

Companion 使用使用者本機 CLI 登入態，不進 backend/worker container。修改常駐 Companion 程式後需重啟進程；正式驗收要比對進程啟動時間與部署程式版本。

