## ADDED Requirements

### Requirement: RPT-008 重分類後報表鏈完整

系統 SHALL 在欄位重分類後成功刷新 report base 與 applicant expanded view，所有依賴欄位的報表須明確產出或回報可解釋錯誤。

#### Scenario: 執行最小 DB smoke

- **WHEN** 0045/0046 套用後刷新 derived
- **THEN** `patent_type`、`document_kind`、展開申請人與至少一個 0046 搬移欄位 SHALL 存在且有代表性非空值
- **AND** `applicant_ranking` 與 `ipc_main_distribution` SHALL 可執行

#### Scenario: 選定報表漏產

- **WHEN** 使用者選定一個 report key 但產製流程沒有輸出
- **THEN** 結果 SHALL 列出 missing report 與原因
- **AND** 不得以整體 succeeded 隱藏漏產

