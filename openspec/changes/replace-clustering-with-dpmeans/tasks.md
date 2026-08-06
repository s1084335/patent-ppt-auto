## 1. 基準與演算法契約

- [ ] 1.1 固定技術/功效雙通道樣本、embedding/model 版本與現行 MiniBatchKMeans 輸出作為比較基準
- [ ] 1.2 定義 cosine Online DP-Means 的向量正規化、PCA、lambda 推導、樣本順序、seed、outlier 與空/小樣本行為
- [ ] 1.3 定義 artifact schema/version、cluster run metadata 與舊 run 的讀取相容策略

## 2. TDD 實作

- [ ] 2.1 Red：新增距離、建群門檻、中心更新、決定性、雙通道與資料驅動 lambda 的單元測試，保存失敗原因
- [ ] 2.2 Green：以最小 Online DP-Means 核心實作通過演算法測試，不先改 UI 或移除現行引擎
- [ ] 2.3 Red：新增 calibrate/finalize/incremental、artifact round-trip 與新舊版本讀取整合測試
- [ ] 2.4 Green：接上 clustering job、repository、topic label 與 API，確保技術/功效結果分離且可追溯
- [ ] 2.5 Refactor：全綠後抽離共用向量前處理並移除已無用途的 K 選擇路徑；保留必要 rollback feature flag

## 3. 驗證與輸出

- [ ] 3.1 比較基準與 DP-Means 的群數、singleton 比例、穩定度、人工可讀性與執行時間，不以單一分數宣稱成功
- [ ] 3.2 執行 clustering/topic/API 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.3 產出 cluster artifact、run metadata、技術/功效 topic labels 與代表性 UI/API 結果，確認重跑可再現
- [ ] 3.4 記錄未測規模與效能風險，經使用者確認群組品質與回復方案後才 archive
