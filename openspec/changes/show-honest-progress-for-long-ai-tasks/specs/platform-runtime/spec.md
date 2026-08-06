## ADDED Requirements

### Requirement: PRT-013 長時間 AI 任務進度不得虛構

系統 SHALL 對可量測階段顯示真實百分比，對無法可靠估算剩餘工作的 AI CLI 階段顯示不確定進度、目前 stage 與已執行時間；不得以固定遞增或長時間停住的假百分比暗示可預測完成度。

#### Scenario: AI CLI 進入不可量測階段

- **WHEN** 工作進入無可靠完成比例的外部 CLI 階段
- **THEN** 任務卡 SHALL 切換為 indeterminate 狀態
- **AND** 顯示目前 stage 與單調遞增的已執行時間
- **AND** 不顯示推測的剩餘時間或虛構百分比

#### Scenario: 長時間仍有 heartbeat

- **WHEN** 百分比未變但工作持續送出有效 heartbeat
- **THEN** 系統 SHALL 顯示工作仍在執行
- **AND** 不得只因進度值未變就標記失敗或卡死

#### Scenario: SSE 斷線後改用查詢恢復

- **WHEN** 任務進行中 SSE 斷線並由 polling 或重連恢復
- **THEN** 任務卡 SHALL 恢復相同工作目前的 stage、開始時間與狀態
- **AND** elapsed time 不得重設為零或倒退

#### Scenario: 多筆 AI 工作同時執行

- **WHEN** 使用者同時追蹤多筆長時間 AI 工作
- **THEN** 每筆任務 SHALL 維持自己的 stage、elapsed time 與終結狀態
- **AND** 一筆工作更新不得覆蓋另一筆工作

#### Scenario: 工作到達終結狀態

- **WHEN** 工作成功、失敗或取消
- **THEN** indeterminate 動畫與 elapsed timer SHALL 停止
- **AND** 顯示真實終結狀態與可用錯誤資訊
