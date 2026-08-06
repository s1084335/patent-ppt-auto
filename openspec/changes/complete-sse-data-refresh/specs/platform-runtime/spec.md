## MODIFIED Requirements

### Requirement: PRT-005 任務事件推播

系統 SHALL 經 PostgreSQL notification 與 FastAPI SSE 推播工作狀態變化；前端斷線時可重新連線，收到成功終結事件時依唯一 mapping 刷新受影響資料區塊。

#### Scenario: 背景工作完成

- **GIVEN** 使用者正在前端等待一筆工作
- **WHEN** 工作狀態改為成功終結狀態
- **THEN** SSE SHALL 推送最新狀態
- **AND** 前端任務狀態 SHALL 不需手動重新整理即可更新
- **AND** 與該 job type 相關的可見資料區塊 SHALL 自動刷新

#### Scenario: 背景工作失敗

- **WHEN** 工作狀態改為 failed 或 cancelled
- **THEN** 前端 SHALL 更新任務狀態與錯誤
- **AND** 不以失敗結果刷新正式資料區塊

#### Scenario: 匯入送出後關閉對話框

- **WHEN** 使用者送出匯入檔案並關閉匯入對話框
- **THEN** 上傳百分比與後續 job stage SHALL 在共用任務進度區持續顯示
- **AND** 完成或失敗狀態不得因 modal 關閉而遺失
- **AND** 匯入進度不得破壞 clustering、report 等其他任務的狀態顯示
