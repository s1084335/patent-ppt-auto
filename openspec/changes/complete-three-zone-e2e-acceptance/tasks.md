## 1. 隔離與證據基建

- [ ] 1.1 定義 E2E run id、測試 DB/storage/workspace preflight、fixture、manifest 與 evidence directory
- [ ] 1.2 Red：新增正式 DB/storage 目標拒絕、manifest 外資料不得清理與無核准不得 cleanup 測試
- [ ] 1.3 Green：完成隔離 setup/teardown dry-run、版本記錄與 Playwright 固定 browser/font/viewport 設定

## 2. 匯入區 E2E

- [ ] 2.1 Red：新增瀏覽器上傳、upload/job 進度、成功統計、錯誤與 workspace 歸屬 E2E，確認現況缺口
- [ ] 2.2 Green：只補必要測試 helper/fixture；若發現產品 bug，另建 change，不在本 change 偷修
- [ ] 2.3 對帳 inserted/matched/updated/patent_ids、workspace members、source/hash 與測試 manifest

## 3. 瀏覽與分類 E2E

- [ ] 3.1 新增全庫/workspace、26 欄、正規化/原文、連結、代表圖 lazy load、分頁與橫向捲動桌面/行動 E2E
- [ ] 3.2 新增技術/功效主題、主題專利、排除/復原、pending review 與 SSE refresh E2E
- [ ] 3.3 加入 console/network error、bounding-box overlap、文字裁切、非空圖片與 snapshot state 程式化檢查
- [ ] 3.4 Refactor：收斂 page objects/helpers，測試以 observable state 等待，不用固定 sleep

## 4. 驗證與人工閘門

- [ ] 4.1 執行 deterministic 全套、相關 API/DB 回歸與 `scripts/verify_module.py`；real Companion profile 明確標示 opt-in/未測
- [ ] 4.2 保存全部三區桌面/行動 screenshots、trace、console/network、API/SQL summary、manifest 與未測項目
- [ ] 4.3 先展示測試匯入結果與 exact cleanup 候選；取得使用者明確同意後才清除測試資料
- [ ] 4.4 使用者逐區驗收證據後才 archive；任何產品缺陷均另開 regression change
