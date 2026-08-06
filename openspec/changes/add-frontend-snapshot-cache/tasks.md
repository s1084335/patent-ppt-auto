## 1. 契約與基準

- [ ] 1.1 盤點 browse、classification、topics 與 latest report content 的現行 API/query/store 流程及重複請求基準
- [ ] 1.2 定義 snapshot envelope、key、content version、TTL 保底與 workspace/import/cluster/report 世代失效矩陣
- [ ] 1.3 決定快照是否只在前端保存或需要後端 persistence，並記錄容量、權限與清除策略

## 2. TDD 實作

- [ ] 2.1 Red：新增 snapshot id 穩定性、世代改變、空 payload、序列化與權限測試，保存失敗原因
- [ ] 2.2 Green：建立集中 snapshot metadata/service 並接入最小 API 路徑
- [ ] 2.3 Red：新增 stale-while-revalidate、競態、workspace 切換、錯誤保留最後成功版本與 latest-response-wins 前端測試
- [ ] 2.4 Green：在 query/store 層完成 envelope cache、狀態與失效，不讓 view 拼接不同版本資料
- [ ] 2.5 Refactor：全綠後移除各頁重複 reload/cache 邏輯並保留無快取回退

## 3. 驗證與輸出

- [ ] 3.1 執行 API/service/frontend 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.2 以桌面與行動瀏覽器驗證 browse、classification、topics、latest report content 的 loading/stale/refreshing/error 狀態
- [ ] 3.3 保存 network trace、snapshot metadata 與 workspace/cluster/report 切換前後畫面，確認無跨 workspace 污染
- [ ] 3.4 回報快取命中、請求數、已知限制與未測項目，經使用者驗收後才 archive
