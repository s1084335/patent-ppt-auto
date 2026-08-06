## ADDED Requirements

### Requirement: PRT-008 外部物件與工作狀態一致

系統 SHALL 讓 job terminal transition 與暫存物件 cleanup 可重入且可追蹤；cleanup 失敗不得把未完成匯入冒充成功，也不得讓已完成業務結果因單次刪除失敗遺失。

#### Scenario: 匯入成功但刪除暫時失敗
- **WHEN** importer 與資料提交成功，但 object store 刪除回傳暫時錯誤
- **THEN** 系統 SHALL 保存 cleanup-pending 證據並安排補償，不得重新執行匯入造成重複資料
