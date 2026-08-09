## ADDED Requirements

### Requirement: RPT-009 法律狀態專用彙總與非阻塞背景刷新

系統 SHALL 僅在「專利狀態分析」中把詳細 `legal_status` 映射為彙總分類：`已申請`、`已公開`、`審查中` 對應 `pending`；`已核准` 對應 `alive`；`放棄`、`核駁`、`撤回`、`已失效`、`屆滿失效` 對應 `dead`；空值或無法辨識值對應 `unknown`。該對照 SHALL 由後端單一來源提供。

#### Scenario: 專利狀態分析使用一致分類
- **WHEN** 系統產生專利狀態分析資料或圖表
- **THEN** 每筆詳細狀態 SHALL 依唯一對照歸入正確分類
- **AND** 報表、API 與前端不得各自定義不同對照

#### Scenario: 儲存後只排程目前範圍的狀態分析
- **GIVEN** TW 狀態已成功提交
- **WHEN** 系統完成核心值與報表投影更新
- **THEN** 系統 SHALL 立即在背景排程目前選定 workspace 的專利狀態分析刷新
- **AND** SHALL NOT 因此排程其他 report key
- **AND** 前端 SHALL 留在原畫面並以非阻塞提示顯示刷新進度

#### Scenario: 背景刷新失敗不回滾狀態
- **GIVEN** 狀態目前值與歷程已成功提交
- **WHEN** 專利狀態分析 enqueue 或執行失敗
- **THEN** 已保存狀態與歷程 SHALL 保持不變
- **AND** 前端 SHALL 顯示刷新失敗與重試操作

#### Scenario: 重試只刷新專利狀態分析
- **WHEN** 使用者重試失敗的背景刷新
- **THEN** 系統 SHALL 重新排程相同 workspace 的專利狀態分析
- **AND** SHALL NOT 再次寫入 `legal_status` 或歷程
