# Design: 品質自動化

## Context

專案現有 pytest 與 `scripts/verify_module.py`，規則門檻在 AGENTS.md；尚無 `.python-version`、CI workflow、ruff/mypy 專案設定。部分契約已有 tests，但前端 `PATENT_COLUMNS`、API schema、report/PPT consumer 尚無單一結構化輸出。

## Goals / Non-Goals

**Goals:** 乾淨環境可重跑、增量守門、正式 DB 防護、跨層契約 diff、證據 artifact。

**Non-Goals:** 不一次修 742 個歷史 lint；不複製規則門檻；不讓 DB/AI 必要測試默默假綠。

## Decisions

### 1. CI 分 fast required 與 opt-in integration

required：version、OpenSpec strict、diff-scoped ruff/mypy、無 DB tests、contract checks。integration：隔離 PostgreSQL、browser、真 Companion 各自有前置與明確 skipped output。避免每個 PR 依賴外部服務，也不把未跑說成通過。

### 2. 漸進式 type/lint 以 changed scope 為 blocking

設定檔固定規則，但 blocking 先看新增／修改範圍；baseline 數量獨立報告。每次擴張 typed package 都是明確 change，不用全庫 ignore 掩蓋問題。

### 3. Producer 產契約，consumer 比對

後端輸出 JSON schema/report registry/export input contract；前端/PPT 測試解析消費端引用做 diff。不能直接 import 時走生成 artifact，不人工抄清單。

### 4. verify_module 是交付 orchestrator

CI 與人工驗收呼叫既有 script 或共用底層命令；AGENTS.md 是門檻唯一來源。script output 保存 commit、base、paths、tests、skips。

## Files And Pipeline

- `.python-version`、`pyproject.toml` tool sections、lockfile（若依賴改變）。
- `.github/workflows/` 或實際採用 CI 目錄。
- `scripts/` contract export/compare 與 `verify_module.py` 最小必要擴充。
- `tests/` mutation/contract tests。

## Output And Test Evidence

- CI artifacts：spec/lint/type/test/coverage/contract summaries 與 skipped manifest。
- mutation smoke：故意破壞 requirement、lint、type、欄位與測試，對應 stage 真正紅。
- 正式 DB 防護：production-like URL 在 collection/startup 即阻擋。

## Risks / Trade-offs

- [初次 CI 太慢] → fast/integration 分層與 cache，不刪必要測試。
- [baseline 掩蓋新錯] → changed-line blocking + baseline report。
- [契約生成檔漂移] → CI 重產後要求 git diff 為零或直接 runtime compare。

## Migration Plan

先版本＋OpenSpec＋無 DB tests，接著 ruff、局部 mypy、契約 checks，最後 DB/browser profiles。每一步先 mutation 驗證守門有效，再設 required；失敗可暫取消該 check 的 required 狀態，但不得刪規則或假綠。
