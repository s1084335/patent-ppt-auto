## ADDED Requirements

### Requirement: ING-011 TW 人工狀態不受空來源覆蓋

WIPS 匯入的狀態來源不涵蓋 TW 時，系統 SHALL 將 incoming 空狀態視為無更新，MUST NOT 清除已存在的人工 `legal_status`，且 MUST NOT 將人工值或人工歷程回寫至 raw source。

#### Scenario: 重匯 TW 空狀態保留人工值
- **GIVEN** 一件 TW 專利已有人工登錄的 `legal_status`
- **AND** 新匯入列的狀態為 NULL、空字串或只含空白
- **WHEN** importer 命中該既有專利
- **THEN** 既有 `legal_status` SHALL 保持不變
- **AND** `legal_status_history` SHALL 不新增匯入事件

#### Scenario: Raw source 保持來源原貌
- **WHEN** 使用者完成人工狀態登錄
- **THEN** `raw_layer.raw_records` SHALL 保持原始 WIPS 狀態值
- **AND** 人工狀態與歷程 SHALL 只存在 core layer
