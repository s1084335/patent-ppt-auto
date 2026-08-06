# Design: 待複核批次裁決

## Context

主前端已有 exclusion review 清單與逐筆 keep/confirm；API body 已使用 `patent_ids` 陣列，service contract 規定 pending 不影響分析、confirm 才 excluded 並移除 assignment。缺口是前端選取、過時防護與批次結果呈現。

## Goals / Non-Goals

**Goals:** 單 request 批次、選取可見、過時拒絕、失敗不丟選取、維持人工護欄。

**Non-Goals:** 不新增 AI 決策；不重跑模型；不實作 topic merge suggestions。

## Decisions

### 1. Selection 以 patent_id + list version 綁定

前端 set 只保存目前 workspace/list version 的 IDs；切 workspace、filter 或收到新 snapshot/SSE 世代時清除或要求重選。不能只靠畫面 checkbox，避免 DOM 更新後送錯資料。

### 2. API 維持批次 endpoint 與整體 transaction

沿用 keep/confirm endpoints 的陣列 body。Service 先鎖／重查 pending IDs，再一次 transaction 套用。混合無效 ID 採明確 rejected 清單；不得逐筆 HTTP N+1。

### 3. Result 以 processed/rejected 分離

前端只移除 processed，rejected 保持選取並顯示原因。Transport/transaction failure 不清任何選取。0 筆提交在 client/server 都拒絕。

## Code And State Boundaries

- UI：`backend/app/static/index.html` exclusion review render/state/actions。
- API/service：`backend/app/api/workspaces.py` 與 clustering exclusions service。
- refresh：與 snapshot/SSE change 共用 invalidation，不另定義第二份 mapping。

## Output And Test Evidence

- 回應：requested/processed/rejected IDs/counts、decision、list version（不回敏感內容）。
- 單元/API：0/1/N、duplicate、wrong workspace、already handled、rollback、N+1 guard。
- 前端/browser：全選、取消、keep/confirm、過時、部分拒絕、SSE refresh。
- DB：pending/kept/excluded/assignments/artifact 前後對帳。

## Risks / Trade-offs

- [清單更新後誤送] → list version + server recheck。
- [大量 IDs payload] → UI 分頁／合理上限，仍單 request。
- [部分成功語意複雜] → transaction 預設 all-valid batch；資料已被其他人處理者列 rejected，不改其他有效項的定案需由 API 測試固定。

## Migration Plan

先鎖現有 API 的陣列與 transaction 測試，再加入 result schema/list version，最後開前端多選。既有逐筆按鈕可在過渡期保留，確認批次實機後再決定是否簡化。
