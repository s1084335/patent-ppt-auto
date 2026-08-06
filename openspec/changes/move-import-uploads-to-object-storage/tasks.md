## 1. 基準與切換契約

- [ ] 1.1 記錄現行 `blob_id` API/job/worker/terminal cleanup、migration head、測試基準與目標環境 DB/storage inventory
- [ ] 1.2 確認目標環境採用的 S3 相容 endpoint/bucket/secret 管理與 object-store activation flag，不把 provider 寫死進業務碼
- [ ] 1.3 定義 payload 雙讀、object key prefix、terminal/retry 決策表與 drop-table SQL gate

## 2. TDD：Object store 與上傳

- [ ] 2.1 Red：新增 key traversal、設定缺失、串流 put/get、hash mismatch、冪等 delete 與 secret redaction 測試並記錄真實失敗
- [ ] 2.2 Green：實作最小 object-store port、fake adapter 與 S3-compatible adapter，使單元測試通過
- [ ] 2.3 Red：新增 upload API object-key payload、大小上限、中途失敗清理與 DB 不寫 bytea 的 API 測試
- [ ] 2.4 Green：接上串流 upload/hash 與單一路徑 feature flag，維持既有 response/匯入意圖契約

## 3. TDD：Worker、生命週期與遷移

- [ ] 3.1 Red：新增 worker download/hash、retry 保留、terminal cleanup、cleanup-pending 補償及舊 `blob_id` job 測試
- [ ] 3.2 Green：完成雙讀 worker 與 terminal/orphan lifecycle，避免 delete 失敗重跑 importer
- [ ] 3.3 Red：新增 migration upgrade/downgrade 與「仍有舊 job 時禁止 drop」契約測試
- [ ] 3.4 Green：在舊 job 歸零後提供 drop `import_blobs` migration；未獲使用者核准前不得套正式 DB
- [ ] 3.5 Refactor：全綠後移除重複 DB/object lifecycle 邏輯，保留過渡期必要相容碼與明確移除條件

## 4. 驗證與啟用

- [ ] 4.1 執行 storage/import/job/migration 目標測試、相關回歸與 `scripts/verify_module.py`，揭露未跑的真 provider/DB 項目
- [ ] 4.2 在隔離環境以小檔及 50 MB+ 檔完成 API→worker→import，核對 hash、統計、DB table size、storage objects 與 cleanup
- [ ] 4.3 先展示切換結果、舊 job SQL gate、orphan dry-run 與 rollback；使用者明確驗收後才切正式 writer、drop table 或 archive
