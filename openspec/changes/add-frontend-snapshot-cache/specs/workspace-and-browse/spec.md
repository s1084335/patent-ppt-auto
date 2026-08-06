## ADDED Requirements

### Requirement: WSP-006 瀏覽與分類採 snapshot-first

系統 SHALL 讓專利列表、workspace 列表、分類主題與主題專利在可用時先讀 snapshot，並在 refresh 後維持原本 workspace 與通道 scope。

#### Scenario: 切換分群通道

- **WHEN** 使用者從技術切到功效通道
- **THEN** SHALL 使用功效通道對應 snapshot 或空狀態
- **AND** 不得顯示技術通道的主題作為功效結果

