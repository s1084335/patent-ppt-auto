## ADDED Requirements

### Requirement: PRT-007 保留期清理作業

系統 SHALL 提供清理封存滿保留期專利的背景作業。

該作業 SHALL 支援 dry-run；dry-run SHALL 列出將被刪除的對象且 SHALL NOT 變更任何資料。

該作業 SHALL 具備批次上限與失敗隔離：單筆失敗 SHALL NOT 影響其餘筆，且 SHALL 逐筆回報結果。

該作業 SHALL 預設為停用，需明確啟用才執行。

#### Scenario: 預設不自動刪除

- **WHEN** 系統啟動而未明確啟用保留期清理
- **THEN** 系統 SHALL NOT 刪除任何專利

#### Scenario: dry-run 可預覽

- **WHEN** 以 dry-run 執行清理
- **THEN** 系統 SHALL 回報將刪除的專利清單與件數
- **AND** 資料庫內容 SHALL 完全不變

#### Scenario: 批次上限

- **WHEN** 符合刪除條件者超過批次上限
- **THEN** 系統 SHALL 只處理上限以內的筆數
- **AND** SHALL 回報尚有多少筆待處理

#### Scenario: 單筆失敗不中斷

- **WHEN** 批次中某筆刪除失敗
- **THEN** 其餘筆 SHALL 繼續處理
- **AND** 系統 SHALL 回報該筆的失敗原因

#### Scenario: 刪除結果可追溯

- **WHEN** 清理作業完成
- **THEN** 系統 SHALL 留下可查詢的執行紀錄，含刪除件數、跳過件數與失敗件數
