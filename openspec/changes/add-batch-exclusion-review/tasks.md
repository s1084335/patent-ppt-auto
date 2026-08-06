## 1. 現況與批次語意

- [ ] 1.1 記錄現行逐筆 UI、keep/confirm 陣列 API、pending/excluded/assignment DB 基準與 refresh 行為
- [ ] 1.2 事前固定 0/1/N、duplicate、wrong workspace、already handled、mixed valid/invalid、stale version 決策表
- [ ] 1.3 定義 list version、批次上限、all-valid transaction 與 processed/rejected response

## 2. TDD：Service 與 API

- [ ] 2.1 Red：新增批次 keep/confirm、N+1 guard、transaction rollback、stale/wrong-workspace 與 artifact 不變測試
- [ ] 2.2 Green：以最小 service/API 擴充完成 server recheck、批次 transaction 與明確結果
- [ ] 2.3 Refactor：全綠後共用逐筆／批次裁決核心，維持 AI 不能 confirm 的單一護欄

## 3. TDD：前端選取

- [ ] 3.1 Red：新增 checkbox、全選可見、計數、清除、空選取、keep/confirm 確認與單 request 測試
- [ ] 3.2 Green：完成 workspace/list-version 綁定 selection state 與批次操作
- [ ] 3.3 Red：新增 SSE/snapshot refresh、部分拒絕、transport failure 後選取保留測試
- [ ] 3.4 Green：只移除 processed rows，rejected/failed 保留選取與可理解原因

## 4. 驗收

- [ ] 4.1 執行 exclusion/service/API/frontend 目標測試、相關回歸與 `scripts/verify_module.py`
- [ ] 4.2 以隔離 workspace 實測 0/1/多筆 keep/confirm/過時/失敗，SQL 對帳 reviews、exclusions、assignments 與 artifact hash
- [ ] 4.3 保存畫面、network request/response、DB 前後與未測項目；使用者驗收批次治理後才 archive
