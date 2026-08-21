## 1. 支援矩陣與供應鏈

- [ ] 1.1 定義支援 Windows 版本、CPU/RAM/disk、管理權限、連線/離線模式、Docker/runtime 與 GPU 政策
- [ ] 1.2 盤點 backend、worker、frontend、migration、模型與第三方授權，建立鎖版 manifest、checksum 與 SBOM
- [ ] 1.3 定義程式、資料、artifact、log、secret 目錄及 install/upgrade/rollback/uninstall 保留規則

## 2. TDD 實作

- [ ] 2.1 Red：新增 manifest/checksum、版本比較、preflight、設定遮罩與命令組裝測試，保存失敗原因
- [ ] 2.2 Green：完成可重現 bundle build 與最小命令列 install/configure/start/health 流程
- [ ] 2.3 Red：新增 migration failure、服務啟動失敗、checksum 錯誤、升級與 rollback 測試
- [ ] 2.4 Green：完成 transaction-like 安裝狀態、診斷輸出與可行的 rollback；不可逆 migration 必須先阻擋或備份
- [ ] 2.5 Refactor：全綠後建立安裝 UI/包裝層，核心流程仍共用同一組可測命令

## 3. 驗證與輸出

- [ ] 3.1 在乾淨 Windows VM 執行 install、重裝、upgrade、失敗回復、uninstall 與資料保留案例
- [ ] 3.2 執行 packaging/runtime 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.3 安裝後完成 backend、worker、DB head、frontend、模型、匯入小樣本、job 與 artifact write/read 健康檢查
- [ ] 3.4 交付安裝包、manifest/checksum/SBOM、遮罩後 log、health report 與已知限制，由使用者驗收後才 archive
