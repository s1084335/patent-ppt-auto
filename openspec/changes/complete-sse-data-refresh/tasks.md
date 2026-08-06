## 1. 事件與刷新矩陣

- [ ] 1.1 盤點所有 job type、terminal status、現行 SSE payload、消費頁面與應刷新資源，建立事件到 resource 矩陣
- [ ] 1.2 定義 event id、workspace、job type、status、completed time、resource invalidation、heartbeat 與重連游標契約
- [ ] 1.3 定義重複、亂序、斷線、workspace 切換與 API refresh 失敗時的 UI 行為

## 2. TDD 實作

- [ ] 2.1 Red：新增 commit 後 terminal event、schema、權限、heartbeat 與 Last-Event-ID/補償刷新後端測試
- [ ] 2.2 Green：最小補齊 producer 與 SSE route，使 succeeded 只在 persistence 成功後發布
- [ ] 2.3 Red：新增 event 去重、resource debounce、in-flight 合併、亂序拒絕、重連與 workspace 隔離前端測試
- [ ] 2.4 Green：完成 event client dispatch 與 query/table invalidation，資料仍由權威 API 重取
- [ ] 2.5 Refactor：全綠後集中重連與 invalidation 邏輯，移除頁面內重複 listener

## 3. 驗證與輸出

- [ ] 3.1 執行 jobs/events/API/frontend 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.2 實跑匯入、分群、AI、報表成功與失敗 job，確認所有矩陣中的表格/報表狀態無手動 reload 即正確更新
- [ ] 3.3 模擬斷線、重連、重複事件與快速 workspace 切換，保存 SSE timeline、network trace 與畫面證據
- [ ] 3.4 回報未覆蓋頁面與瀏覽器限制，經使用者驗收自動刷新行為後才 archive
