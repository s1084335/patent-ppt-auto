## ADDED Requirements

### Requirement: WSP-008 待複核可批次裁決

系統 SHALL 允許使用者多選目前 workspace 的 pending exclusion reviews，批次 keep 或 confirm；選取狀態須顯示數量、可清除，並在清單世代改變時拒絕過時提交。

#### Scenario: 批次保留多筆
- **WHEN** 使用者選取多筆 pending 候選並確認 keep
- **THEN** 系統 SHALL 以單次批次操作保留選取項、回報實際處理數並只移除成功列

#### Scenario: 清單刷新後提交舊選取
- **WHEN** workspace 或 review list version 已改變
- **THEN** 系統 SHALL 拒絕過時選取並要求重新確認，不得套用到同 ID 以外資料

### Requirement: WSP-009 批次錯誤不丟失選取

系統 SHALL 對空選取、無效 ID、非 pending、權限錯誤與 transaction failure 回傳明確結果；失敗或部分失敗時前端 MUST 保留未處理選取。

#### Scenario: 混合有效與已處理 ID
- **WHEN** 批次包含 pending 與已非 pending 的 ID
- **THEN** 回應 SHALL 明列實際處理與拒絕項目，前端不得顯示全部成功
