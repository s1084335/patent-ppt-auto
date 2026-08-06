## ADDED Requirements

### Requirement: ING-007 Delimited 格式正確解析

系統 SHALL 以標準 CSV parser 保留引號內換行，並在 Sniffer 不可靠時依副檔名與可驗證欄位結構降級。

#### Scenario: CSV 欄位含換行

- **GIVEN** 一筆 CSV 的引號欄位包含換行
- **WHEN** 解析檔案
- **THEN** 該內容 SHALL 保持在同一 record
- **AND** 後續列不得位移

#### Scenario: Sniffer 猜錯 delimiter

- **WHEN** 自動偵測結果無法形成合理欄位
- **THEN** 系統 SHALL 使用安全 fallback 重試
- **AND** 無法解析時回報明確錯誤而非產生錯欄資料

### Requirement: ING-008 XML 安全串流

系統 SHALL 以串流方式解析 XML，拒絕外部實體與不受控資源展開，並保持不同命名空間的欄位抽取能力。

#### Scenario: XML 含外部實體

- **WHEN** 匯入 XML 宣告外部實體
- **THEN** parser SHALL 不解析外部資源
- **AND** 匯入 SHALL 安全失敗或忽略不安全實體

#### Scenario: 大型 XML

- **WHEN** 匯入大型多筆 XML
- **THEN** 系統 SHALL 逐元素處理並釋放已完成節點
- **AND** 記憶體使用不隨完整檔案大小等比例保留

