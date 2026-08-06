## 1. 契約基準與 Red

- [ ] 1.1 盤點現行 workflow/job progress、AI Companion heartbeat、SSE、polling 與前端任務卡欄位，建立 PRT-013 scenario 到程式／測試落點矩陣
- [ ] 1.2 Red：新增 determinate／indeterminate、舊 payload 缺新欄位、server time 與 terminal freeze 的 repository/API contract tests，實跑並記錄真實失敗原因
- [ ] 1.3 Red：以 fake timers 新增前端 indeterminate、elapsed 單調、SSE→polling、重連、多任務隔離與終結停止測試，實跑並確認不是空殼失敗
- [ ] 1.4 Red：新增 AI fake runner 的 stage／heartbeat transition 測試，涵蓋成功、失敗、取消與長時間無百分比情境

## 2. 最小 Green

- [ ] 2.1 Green：以 additive contract 補齊 progress mode、stage 與 server-time anchors，維持舊 producer／client 相容
- [ ] 2.2 Green：讓 AI Companion 在 CLI 啟動、主要階段、artifact persistence 與終結點更新真實 stage／heartbeat，不推算剩餘時間
- [ ] 2.3 Green：讓 Job API、SSE 與 polling 共用同一工作投影，不在 transport 各自重算 elapsed 或 mode
- [ ] 2.4 Green：前端共用任務卡依 mode 呈現真實百分比或 indeterminate＋elapsed，並以工作 identity 隔離多任務
- [ ] 2.5 Green：實跑 1.2～1.4 全部目標測試直到通過，每個切片通過後停止並記錄結果

## 3. Refactor 與回歸

- [ ] 3.1 Refactor：目標測試全綠後收斂重複 progress payload、timer 與 task-card rendering 邏輯，不改已通過行為
- [ ] 3.2 執行 job repository、AI Companion、SSE/API、frontend 相關回歸，確認匯入、分群與報表 determinate progress 不退化
- [ ] 3.3 執行 `scripts/verify_module.py`，回報 lint、type、複雜度、新增行覆蓋率與無法執行項目

## 4. 整合與實物驗收

- [ ] 4.1 以可控制的短／長 fake CLI 驗證 heartbeat、elapsed、失聯、SSE 斷線重連、polling fallback 與兩筆同時工作，保存 event timeline
- [ ] 4.2 執行一筆真實 AI CLI smoke，確認不可量測階段不顯示假百分比，成功／失敗後 timer 正確停止
- [ ] 4.3 以桌面與行動 viewport 擷取任務卡畫面，檢查文字、進度條與錯誤狀態不重疊，揭露未驗環境
- [ ] 4.4 使用者驗收 PRT-013 全部 scenario 後才勾完並進入 archive；不得因單元測試通過宣告實機完成
