## 1. 現況與資料契約

- [x] 1.1 盤點 A1-A4 已合併程式、migration head、實際連線目標與 `patents`、`patent_people`、`patent_attributes` 現行 schema，記錄基準而不把歷史測試數當成現況證據
- [x] 1.2 由 importer、mapping、derived、clustering、report、comparison 與 API 使用點產生欄位用途矩陣，確認「會被運算、分群、篩選、顯示或輸出」者只進核心表
- [x] 1.3 定義 selected、persisted、derived、rendered 四個欄位集合及允許差異，將不一致列為驗收失敗

## 2. TDD 實作

- [x] 2.1 Red：新增 schema/mapping round-trip、people role、attribute fallback 與下游欄位集合測試，保存預期失敗原因
- [x] 2.2 Green：以最小修改補齊 mapping、importer、repository、migration 與下游讀取，使新測試通過
- [x] 2.3 Refactor：只在測試全綠後移除重複欄位路徑與過渡相容碼，維持既有 identifier/dedupe 語意

## 3. 驗證與輸出

- [x] 3.1 在隔離測試資料庫套用完整 migration，驗證 upgrade、約束、索引、FK 與必要 rollback/forward-fix 路徑
- [x] 3.2 匯入可追查的小型 WIPS 樣本，先保留並回報測試資料，不在未獲使用者同意前清除
- [x] 3.3 以 SQL 對帳來源列、核心欄位、people、attributes、derived 與報表輸出，確認 selected/persisted/rendered 集合一致
- [x] 3.4 執行目標 pytest、相關模組回歸與 `scripts/verify_module.py`；記錄未測項目、環境與結果
- [ ] 3.5 提交 schema diff、欄位用途矩陣、SQL 對帳與代表性輸出供使用者驗收；明確同意前不得 archive change

## 執行紀錄（2026-08-06，Claude；細節見 work-log 與 output/_verify/p0/）

- 1.1–1.3：欄位用途矩陣＋四集合（selected 40/19/87、derived 43、rendered 29）
  → `output/_verify/p0/field_usage_matrix.md`；契約測試常駐
  `tests/test_core_field_reclassification.py::FieldUsageMatrixTests`（4 條，
  ⚠ 首跑即過＝**確認性**測試——證明現狀合規並守住漂移，不是抓到新缺陷）。
- 2.1–2.3：重分類本體的 Red→Green 已於同日稍早完成（Red 42 failed 有紀錄；
  含實機抓到的 psycopg 參數名含括號 bug 與 regression tests）。
- 3.1：**隔離庫**＝Supabase 同實例 `migcheck_0046`（遵守「不起本機容器」）。
  空庫 0001→0046 全鏈通過；**帶資料** downgrade 0046→0045（attributes 欄回復、
  44 筆回填）→ 再 upgrade（core 值無損回歸、EPC 成對 1 筆保持）。
- 3.2：樣本重匯改在**隔離庫**執行（不污染正式資料）：滑雪機.xlsx 55 列
  `inserted=55`（INSERT 路徑；正式庫另有 UPDATE 路徑 22 判準證據）、
  0 warnings、16 欄非空數與正式庫基準**逐欄相同**。
  ⚠ 隔離庫**保留未刪**，是否清除待使用者裁決。
- 3.3：正式庫唯讀對帳 → `output/_verify/p0/sql_reconciliation.md`，
  一對一關聯／孤兒／16 欄非空 **0 問題**。
  ⚠ 隔離庫報表 smoke `applicant_ranking` 34 列 vs 正式庫 25 列：
  差異來自 company_aliases 人工累積（正式庫 74 筆 confirmed、隔離庫僅匯入時建 1 筆），
  屬預期，非缺陷。
- 3.4：`verify_module` 量測項全數達標（新增行 lint 0）；範圍回歸 151 passed。
- 3.5：⬜ 證據已備妥，**待使用者逐項確認後才 archive**。
