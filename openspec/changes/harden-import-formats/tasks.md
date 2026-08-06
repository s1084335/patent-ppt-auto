## 1. 格式矩陣與安全邊界

- [ ] 1.1 蒐集允許保存的 CSV/XML/Excel 小型 fixtures，建立來源、編碼、分隔符、欄名、日期與 identifier 格式矩陣
- [ ] 1.2 明確定義不支援格式、檔案/列數/欄位上限、壓縮檔政策、錯誤列輸出與 XXE/entity 安全規則
- [ ] 1.3 對照 mapping、dedupe、schema 與 derived/report 依賴，確認格式新增不改變 identifier merge truth

## 2. TDD 實作

- [ ] 2.1 Red：新增 BOM、編碼、引號換行、delimiter detection、缺欄、重複欄、巨大欄位與 malformed CSV 測試
- [ ] 2.2 Green：以串流 parser 與明確 schema validation 完成 CSV 路徑，錯誤含來源列與欄位但不洩漏敏感內容
- [ ] 2.3 Red：新增 namespace、重複節點、缺節點、XXE/entity、深度/大小限制與 malformed XML 測試
- [ ] 2.4 Green：以禁用外部實體的串流 XML parser 完成受支援格式，不用字串切割模擬解析器
- [ ] 2.5 Refactor：共用 normalization/mapping/error-report pipeline，保留各格式 adapter 的責任邊界

## 3. 驗證與輸出

- [ ] 3.1 對每個格式執行同資料 round-trip，核對 patents、patent_people、patent_attributes、sources 與 dedupe 結果
- [ ] 3.2 執行 importer/mapping/repository 目標測試、相關模組回歸與 `scripts/verify_module.py`
- [ ] 3.3 產出 import summary、逐列錯誤檔與遮罩後 log；先向使用者展示測試匯入結果，不主動清除測試列
- [ ] 3.4 記錄未支援格式與效能上限，取得使用者對格式矩陣及樣本輸出的驗收後才 archive
