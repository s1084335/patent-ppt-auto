## Why

AI 不相干專利待複核已有逐筆 keep/confirm API 與前端，但大量候選只能逐筆操作；API 已接受 `patent_ids` 陣列，前端尚無候選 checkbox、全選與批次送出。這使正常人工治理流程在候選多時成本過高。

## What Changes

- 待複核表加入穩定 checkbox、全選目前可見項、已選計數與清除選取。
- 批次「保留」或「確定排除」前顯示數量與不可混用的明確確認；一次 request 傳選取 `patent_ids`。
- 成功後只移除已處理列並刷新受影響區塊；部分／全部失敗保留選取並顯示逐項可理解結果。
- workspace、篩選、SSE refresh 或清單版本改變時，拒絕提交過時選取。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

- `workspace-and-browse`: 增加待複核候選的多選、批次裁決、過時選取與刷新行為。
- `clustering-and-topics`: 維持 pending 不影響分析、confirm 才排除且不重跑分群的批次一致性。

## Scope

只處理 AI exclusion review 的批次人工裁決；不建立新的 AI 篩選模型或 topic merge suggestion engine。

## Non-goals

- 不允許 AI 直接批次寫成 `excluded`。
- 不用單筆 request 迴圈造成 N+1。
- 不在失敗時靜默清除選取或假裝全部成功。

## Impact

- 前端 exclusion review table/state、既有 keep/confirm API 的批次回應契約、service transaction 與測試。

## Activation

沿用既有 endpoint，先確認 repository/service 對陣列的 transaction 與部分失敗語意；必要時只擴充回應，不新增重複 endpoint。

## Acceptance Gate

以 0、1、多筆、跨頁、過時與混合有效／無效 ID 決策表驗證；確認 keep 不影響 assignments、confirm 只改選取項且不重跑分群，瀏覽器實測後由使用者驗收。
