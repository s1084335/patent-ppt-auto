## ADDED Requirements

### Requirement: CLU-012 法律狀態與分群隔離

系統 MUST NOT 將 `legal_status`、狀態顯示值或 `pending`／`alive`／`dead`／`unknown` 彙總分類用作分群輸入、分群篩選、專利排除條件、embedding freshness 依據或分群工作觸發來源。所有 TW 專利 SHALL 不因法律狀態而被排除於既有分析與分群。

#### Scenario: 狀態登錄不建立分群工作
- **GIVEN** 一件 TW 專利已有既存 cluster assignment
- **WHEN** 使用者首次登錄其法律狀態
- **THEN** 系統 SHALL NOT 建立 clustering、embedding 或 topic 工作
- **AND** 既存 assignment SHALL 保持不變

#### Scenario: 不同狀態仍使用相同分群輸入
- **GIVEN** 兩件專利只有法律狀態不同而分群來源文字相同
- **WHEN** 系統建立分群輸入與 freshness identity
- **THEN** 法律狀態差異 SHALL NOT 改變輸入文字、hash、距離或排除判斷
