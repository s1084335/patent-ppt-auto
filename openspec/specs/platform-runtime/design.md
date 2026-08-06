# Platform Runtime Design

## 架構與資料流

`FastAPI API -> app_layer.workflow_runs -> worker/AI Companion -> app_layer.workflow_outputs` 是唯一工作資料流。一般 worker 的可領類型由 `JOB_TYPES - AI_JOB_TYPES` 推導；AI Companion 直接沿用同一個 `AI_JOB_TYPES`，避免兩份白名單漂移。

工作領取使用 PostgreSQL `FOR UPDATE SKIP LOCKED`。進度、鎖定者、heartbeat、錯誤與重試資訊收在 `worker_state_json`；正式輸出另存 `workflow_outputs`，不塞回狀態列。

## 程式落點

- API 組裝與 router：`backend/app/main.py`、`backend/app/api/jobs.py`、`backend/app/api/events.py`
- 工作契約：`backend/app/db/job_repository.py`
- 一般 worker：`backend/app/worker/runner.py`、`backend/app/worker/handlers.py`
- AI Companion：`backend/app/worker/ai_bridge.py`
- 執行角色：`backend/app/deploy.py`、`Dockerfile`、`docker-compose*.yml`

## 測試證據

- `tests/test_job_repository.py`
- `tests/test_worker_queue_client.py`
- `tests/test_worker_handlers.py`
- `tests/test_worker_main_entrypoint.py`
- `tests/test_api_jobs.py`、`tests/test_api_jobs_unit.py`
- `tests/test_api_events.py`
- `tests/test_ai_job_registration_guard.py`
- `tests/test_deploy_entrypoint.py`

## 輸出契約

- Job API：`run_id`、`run_type`、`status`、request、worker state 與 result projection。
- SSE：工作狀態事件與 Companion 狀態。
- DB：`workflow_runs` 保存控制狀態，`workflow_outputs` 保存版本化輸出。

## 驗收邊界

單元測試可驗證路由、白名單與狀態轉換；正式部署仍需以實際 PostgreSQL、一般 worker、Companion 及 SSE 連線做 smoke。OpenSpec validate 不代替這些檢查。

