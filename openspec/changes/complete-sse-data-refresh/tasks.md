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

- [ ] 3.1 執行 jobs/events/API/frontend 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.2 實跑匯入、分群、AI、報表成功與失敗 job，確認所有矩陣中的表格/報表狀態無手動 reload 即正確更新
- [ ] 3.3 模擬斷線、重連、重複事件與快速 workspace 切換，保存 SSE timeline、network trace 與畫面證據
- [ ] 3.4 回報未覆蓋頁面與瀏覽器限制，經使用者驗收自動刷新行為後才 archive
