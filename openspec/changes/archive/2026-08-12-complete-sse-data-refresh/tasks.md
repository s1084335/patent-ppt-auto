## 1. 事件與刷新矩陣

- [x] 1.1 盤點所有 job type、terminal status、現行 SSE payload、消費頁面與應刷新資源，建立事件到 resource 矩陣（落 design.md「事件×刷新矩陣」）
- [x] 1.2 定義 event id、workspace、job type、status、completed time、resource invalidation、heartbeat 與重連游標契約（pg_notify 無補送→不做游標，改重連補償刷新）
- [x] 1.3 定義重複、亂序、斷線、workspace 切換與 API refresh 失敗時的 UI 行為（design.md「異常行為」）

## 2. TDD 實作

- [x] 2.1 Red：migration 0049 契約測試（run_type/workspace_id/event_id/completed_at、
      不動 trigger、downgrade 還原 fd301）——真 Red：缺檔 6 errors。
      「succeeded 只在 persistence 後發布」由 pg_notify 的 COMMIT 遞送語意天然成立，
      producer 零改動；Last-Event-ID 因 pg_notify 無補送**不做**，改前端補償刷新
- [x] 2.2 Green：`0049_sse_event_metadata`（CREATE OR REPLACE notify_run_change；
      SSE route 為 payload passthrough 零改動）
- [x] 2.3 Red：前端契約 12 failed＋4 errors（mapping 跨層對帳、succeeded 守門、
      event_id 去重、debounce/in-flight、詳情列保留、重連補償）
- [x] 2.4 Green：JOB_REFRESH_TARGETS＋RESOURCE_REFRESHERS＋scheduleResourceRefresh
      ＋refreshBrowsePreservingDetails（data-pid 回開）＋refreshVisibleResources；
      資料一律由權威 API 重取
- [x] 2.5 Refactor：invalidation 收斂單一 mapping；頁面級輪詢（topics 自動刷新、
      classify/finalize/import tick）確認為**動作範疇進度輪詢＋SSE 退化保底**，
      非重複 listener，保留並補分工註記；ai:narrative 沿用既有版本守門路徑不重接

## 3. 驗證與輸出

- [x] 3.1 目標測試 29 passed（migration 契約 6＋listen 連線 6＋前端契約 17）＋
      範圍回歸 491 passed（14 紅全屬既有債：launcher 遺留與 PPT 移除漏網測試，
      引用已刪 ai:report_plan／runner，與本 change 無關、另列待辦）＋
      新增行 ruff 歸零、新函式 CC A、分支全覆蓋
- [x] 3.2 實跑（Playwright，A–F 六段全綠，證據 output/_verify/sse_refresh/）：
      真 refresh_derived job 端到端（worker 實跑→commit→NOTIFY→SSE→自動刷新）；
      report_generate／ai:topic_label／failed 事件以「合成 run 列 UPDATE 打真 trigger」
      覆蓋（trigger→SSE→前端鏈與真 job 完全同路徑，僅 handler 執行不同）。
      ⚠ 未實跑真匯入／分群／AI job（成本與資料殘留；由使用者實機驗收接手）
- [x] 3.3 斷線→30 秒輪詢退化→退避重連（5s 起、上限 60s）→補償刷新，實機驗證；
      重複終結事件由 event_id 去重（單元契約）。⚠ 快速 workspace 切換未實測
      ——刷新函式一律讀當前 state 打權威 API，舊資料 by construction 畫不上來
- [x] 3.4 揭露未覆蓋：case_comparison（比對頁自有輪詢）、匯出頁（僅沿用既有
      narrative 路徑）、SSE 斷線窗內事件永久遺失（由重連補償與 30s 輪詢保底）。
      使用者 2026-08-12 驗收通過（「可以我都驗過了」），archive
