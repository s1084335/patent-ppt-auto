## ADDED Requirements

### Requirement: PRT-007 清理工作可追蹤

系統 SHALL 將耗時 retention/cleanup 以可查詢工作或明確 maintenance run 執行，保存 dry-run／delete mode、統計與錯誤摘要。

#### Scenario: 清理工作中斷

- **WHEN** retention run 在批次中斷
- **THEN** 已處理範圍與下一次可重跑位置 SHALL 可判斷
- **AND** 不得留下整批成功的假狀態

