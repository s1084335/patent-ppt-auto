## ADDED Requirements

### Requirement: RPT-013 選圖資料包完整且同版

系統 SHALL 為使用者選定圖表建立不可變資料包，每張圖包含 chart identity、可供視覺判讀的 artifact、同口徑結構化數據、報表定義、母體／篩選、snapshot/version 與 checksum；不同 snapshot 的圖與數據不得混包。

#### Scenario: 圖與數據版本不一致

- **WHEN** chart artifact checksum／來源版本與其 report data 不一致
- **THEN** 系統 SHALL 阻止 CLI 規劃並要求重新 materialize

### Requirement: RPT-014 唯讀證據查詢受語意目錄約束

系統 SHALL 只允許從核准的報表／證據目錄查詢同一 workspace/analysis snapshot 的白名單欄位、filters 與列數；不得接受任意 SQL、任意 schema/column 或跨 snapshot 查詢。

#### Scenario: 查詢未允許欄位

- **WHEN** CLI 要求目錄未宣告的欄位或 filter
- **THEN** 系統 SHALL 回傳驗證錯誤
- **AND** 不執行退化為 raw SQL 的替代查詢

#### Scenario: 證據超過列數限制

- **WHEN** 查詢結果超過工具上限
- **THEN** 系統 SHALL 回傳截斷／分頁 metadata
- **AND** CLI 不得把部分結果宣稱為完整母體
