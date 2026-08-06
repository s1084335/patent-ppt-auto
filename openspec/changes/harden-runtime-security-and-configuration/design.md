# Design: Runtime 安全與設定硬化

## Context

`get_connection_kwargs()` 在無 `DATABASE_URL` 時預設 localhost；deploy script 雖有檢查，但其他啟動方式可繞過。AI endpoint 採 opt-in token，前端注入點回空。Companion 遇 DB OperationalError 會退避且累加 `db_errors`，但長期斷線仍可能對外顯示 running。

## Goals / Non-Goals

**Goals:** role-aware fail-fast、deployment auth-by-default、遮罩 readiness、Companion degraded/recovery。

**Non-Goals:** 不導入企業 SSO；不重寫 MCP 已完成的 bearer transport；不把 secret 交給 localStorage。report-research 的工具白名單與 DB grants 由 `enable-goal-driven-readonly-report-planning` 定義，本 change 只負責角色設定與 fail-fast 接線。

## Decisions

### 1. 明確 runtime mode，不用「缺設定」推導 local

引入明確 `PATENT_RUNTIME_MODE=local|deployment`（名稱可依既有 settings 收斂）。deployment mode 依 APP_ROLE 驗必填項；local mode 才允許 PG* defaults。這比判斷 hostname/container 更可測且不會誤判。

### 2. Auth 在 server 為唯一 enforcement

所有 AI write endpoints 共用 dependency/middleware。前端 credential 方案優先由同源 reverse proxy/session 注入；若採 bearer，僅保存於記憶體並由單一 `aiAuthHeaders` 消費。UI 隱藏不是安全邊界。

### 3. Health 分 liveness/readiness/degraded

liveness 只代表 process；readiness 驗必要設定與依賴；Companion heartbeat/doctor 帶狀態、連續失敗、last success、redacted category。告警出口消費同一狀態，不另算一套。

### 4. Report-research identity 不得 fallback

goal-driven report CLI 所用 MCP 必須宣告獨立 runtime role 與 reader credential。validator 只檢查設定與 identity 是否符合唯讀 profile；實際工具／grant contract 留在對應 change。缺 reader credential、連到一般 application identity 或 profile 不符時 fail-fast，不得沿用 `DATABASE_URL` 的廣泛權限。

## Code And Configuration Boundaries

- `backend/app/db/connection.py` 與集中 settings validation。
- FastAPI startup、health/readiness、AI auth dependency。
- `backend/app/static/index.html` 單一 credential injection point。
- `backend/app/worker/ai_bridge.py` heartbeat/doctor、report-research MCP entrypoint 與 deployment docs/installer。

## Output And Test Evidence

- 決策表：mode × role × DB/reader credential/token present × dependency health。
- API：401/403、不建立 job；readiness JSON 只含 redacted fields。
- Companion：門檻前 retry、門檻後 degraded、成功後 recovery。
- secret scan：response/log/build artifact/URL/localStorage 零命中。

## Risks / Trade-offs

- [啟用後現有環境起不來] → audit mode 列缺項，逐環境補齊後 enforce；report-research 未設定時功能保持關閉，不得放寬權限。
- [前端 token UX 困難] → 優先同源 proxy/session；不得降回公開端點。
- [短暫 DB 抖動誤告警] → 連續失敗門檻與 recovery event，門檻事前寫入測試。

## Migration Plan

1. 集中 validator/audit；2. health schema；3. Companion degraded；4. server auth 與前端路徑；5. staging enforce；6. production enforce。Rollback 可暫回 audit mode，但 deployment 不得靜默切 local。
