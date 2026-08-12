## ADDED Requirements

### Requirement: WSP-007 自動刷新保留互動狀態

系統 SHALL 在背景工作成功後刷新可見資料，但保留展開、收合、選定 workspace/topic、報表版本與編輯模式等非資料狀態。

#### Scenario: 文獻備註完成

- **GIVEN** 使用者停留在含該專利的瀏覽表格
- **WHEN** `ai:patent_note` 成功完成
- **THEN** 文獻備註 SHALL 自動出現在表格
- **AND** 已展開的其他專利詳情 SHALL 保留

#### Scenario: 使用者停留無關頁面

- **WHEN** 專利備註工作完成但使用者在匯出頁
- **THEN** 前端 SHALL 不立即重抓專利列表

