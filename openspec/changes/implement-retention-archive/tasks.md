## 1. 政策與資料盤點

- [ ] 1.1 盤點 jobs、events、AI/report artifacts、中間輸出與資料庫參照，為每類指定 owner、保留期、封存格式及刪除順序
- [ ] 1.2 定義 legal hold、進行中 job、latest report、workspace 引用與失敗批次的排除條件
- [ ] 1.3 以現行資料庫與 storage 執行唯讀 inventory，記錄筆數、容量、最舊日期與孤兒候選，不進行刪除

## 2. TDD 實作

- [ ] 2.1 Red：新增 cutoff、timezone、legal hold、latest pointer、FK 順序、batch resume 與 dry-run 無副作用測試
- [ ] 2.2 Green：建立 policy registry、inventory/planner 與 dry-run command，預設 destructive mode 關閉
- [ ] 2.3 Red：新增 archive manifest、checksum/read-back、部分失敗、transaction rollback、併發新增與 idempotency 測試
- [ ] 2.4 Green：完成 archive-confirm-delete、小批次 cleaner、audit/metrics 與 resume cursor
- [ ] 2.5 Refactor：全綠後抽離共用 batch lifecycle，保持 DB 與 object storage 狀態可追查

## 3. 驗證與輸出

- [ ] 3.1 在隔離測試資料庫與測試 storage 執行 dry-run，先向使用者展示候選、排除與預估容量結果
- [ ] 3.2 經核准後才執行測試環境 archive/cleanup，核對 manifest read-back、checksum、前後 SQL、FK 與 storage inventory
- [ ] 3.3 執行 retention/repository/artifact 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.4 記錄未測資料類別、production 啟用旗標與 rollback 限制；取得使用者明確驗收前不得啟用 production destructive mode 或 archive change
