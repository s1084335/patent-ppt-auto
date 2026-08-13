# Platform Runtime Specification

## Purpose

定義 FastAPI、一般 worker、AI Companion 與 PostgreSQL 共用工作佇列的現行執行契約。

## Requirements

### Requirement: PRT-001 單一版本化工作佇列

系統 SHALL 以 `app_layer.workflow_runs` 保存工作請求與狀態，並以 `app_layer.workflow_outputs` 保存版本化結果。

#### Scenario: 建立可追蹤工作

- **GIVEN** API 收到合法工作請求
- **WHEN** 系統建立工作
- **THEN** 回傳唯一 `run_id`
- **AND** 狀態可由 Job API 查詢
- **AND** 終結結果可由 workflow output 讀回

#### Scenario: 重複冪等請求

- **GIVEN** 兩次請求具有相同有效 request key
- **WHEN** 第二次建立工作
- **THEN** 系統 SHALL 重用既有工作而非建立重複執行

### Requirement: PRT-002 工作領取與失敗隔離

系統 SHALL 以原子方式領取工作，維護 heartbeat，並能回收逾時工作而不讓兩個 worker 同時執行同一筆工作。

#### Scenario: 多 worker 同時領取

- **GIVEN** 佇列有一筆待執行工作
- **WHEN** 多個 worker 同時嘗試領取
- **THEN** 最多一個 worker 取得該工作

#### Scenario: 工作達終結狀態

- **WHEN** 工作成功、失敗或取消
- **THEN** 狀態 SHALL 收斂為 `succeeded`、`failed` 或 `cancelled`
- **AND** 不再被一般領取流程重新執行

### Requirement: PRT-003 一般工作與 AI 工作分流

系統 SHALL 由一般 worker 執行 deterministic 工作，並由 host-side AI Companion 專門領取 `AI_JOB_TYPES`。

#### Scenario: 一般 worker 遇到 AI 工作

- **GIVEN** 佇列同時存在一般工作與 AI 工作
- **WHEN** 一般 worker 領取下一筆工作
- **THEN** 不得領取需要外部 AI CLI 的工作

### Requirement: PRT-004 健康與就緒狀態

系統 SHALL 提供健康、就緒、工作狀態與 Companion 狀態端點，讓操作端區分 API 存活、資料庫可用、worker 可用與 AI Companion 可用。

#### Scenario: 相依服務不可用

- **WHEN** API 存活但必要相依服務不可用
- **THEN** readiness 回應 SHALL 反映不可用狀態
- **AND** 不得把單純 HTTP 存活誤報為整體可用

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

### Requirement: PRT-006 受保護寫入

系統 SHALL 對指定管理與 AI 寫入端點要求 API token，公開唯讀或健康端點依路由契約維持可用。

#### Scenario: 缺少 token 的受保護請求

- **WHEN** 呼叫者未提供有效 token 存取受保護端點
- **THEN** 系統 SHALL 拒絕請求
