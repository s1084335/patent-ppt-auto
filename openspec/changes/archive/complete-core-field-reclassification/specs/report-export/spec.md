## ADDED Requirements

### Requirement: EXP-007 正式資料完整重產

系統 SHALL 以完成 migration、重匯與 derived refresh 的正式資料重產整份報告，並驗證 artifact 與全部受影響頁面。

#### Scenario: A5 正式交付

- **WHEN** 最小 DB gate 已通過並完成完整報告重產
- **THEN** 所有選定且有資料的報表 SHALL 出現在 report metadata 與 PPT
- **AND** `.pptx`、圖表、report data 與 narratives SHALL 可由 artifact store 重新讀取
- **AND** 全部受影響頁面 SHALL 完成逐頁檢視

