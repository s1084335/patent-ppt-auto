# Clustering and Topics Specification

## Purpose

定義 workspace 雙通道分群、候選校準、人工定案、增量更新、主題治理與排除流程的現行契約，確保技術與功效結果分離、每次 run 可追溯，且自動建議不會繞過人工確認成為正式分類。

## Requirements

### Requirement: CLU-001 固定雙通道來源

系統 SHALL 以技術通道 `wips_independent_claims` 與功效通道 `effect_summary` 分別產生 embeddings、分群與主題狀態，不得混成單一向量空間。

#### Scenario: 技術通道缺少獨立項

- **WHEN** 專利缺少技術通道指定來源欄
- **THEN** 系統 SHALL 將該筆標示為該通道無有效文本
- **AND** 不得靜默 fallback 到功效或其他欄位污染技術分群

### Requirement: CLU-002 校準候選與人工定案

系統 SHALL 先產生可比較的候選方案與指標，只有使用者選定候選後才建立正式主題與 assignments。

#### Scenario: 候選尚未定案

- **WHEN** calibrate 已完成但尚未 finalize
- **THEN** 候選可被檢視與比較
- **AND** 不得被報表當作正式主題

### Requirement: CLU-003 模型與 artifact 可重現

系統 SHALL 保存模型設定、來源通道、workspace、artifact key 與 hash；載入 artifact 時須驗證完整性。

#### Scenario: Artifact hash 不符

- **WHEN** 實體 artifact 與保存的 hash 不一致
- **THEN** 系統 SHALL fail loud
- **AND** 不得用未知模型狀態繼續增量分群

### Requirement: CLU-004 現行增量分群

系統 SHALL 使用既有正式 artifact 對新增文件執行現行 BERTopic/MiniBatchKMeans 增量流程，保留舊專利 assignments 並產生新 run。

#### Scenario: 增量後查詢舊專利

- **WHEN** 新增文件完成增量分群
- **THEN** 舊專利 SHALL 仍可由 run chain 取得主題歸屬
- **AND** 新 run 不得讓舊 assignments 從 UI 或報表消失

### Requirement: CLU-005 主題人工治理

系統 SHALL 支援主題改名、排序、合併、解除合併與歷史查詢；只有成功完成的合併可提供解除操作。

#### Scenario: 合併尚在 queued

- **WHEN** topic merge 工作尚未成功
- **THEN** 歷史 SHALL 顯示真實狀態
- **AND** 不得誤顯示為已完成或提供解除按鈕

### Requirement: CLU-006 AI 輔助不取代人工定案

系統 SHALL 允許 AI 產候選說明與主題中文 label/summary，但正式候選選擇、排除裁決與人工改名仍由使用者決定。

#### Scenario: AI 主題標籤回寫

- **WHEN** AI 回傳合法主題標籤
- **THEN** 系統 SHALL 依 workspace、run、source field 與 topic key 寫回
- **AND** 不得跨通道覆蓋另一組主題

### Requirement: CLU-007 排除不刪核心資料

系統 SHALL 將 AI 不相干判讀保存為 pending review，只有人工確認後才從分析範圍排除，且可復原。

#### Scenario: AI 判定不相干

- **WHEN** AI 回傳不相干判讀
- **THEN** 專利 SHALL 進待複核狀態
- **AND** 不立即刪除 assignment 或核心專利

#### Scenario: 使用者保留待複核專利

- **GIVEN** 專利處於 pending review
- **WHEN** 使用者選擇保留
- **THEN** 系統 SHALL 移除該筆待複核紀錄
- **AND** 保留原 workspace 成員與主題指派

#### Scenario: 使用者確認排除

- **GIVEN** 一般 workspace 內的專利處於 pending review
- **WHEN** 使用者確認排除
- **THEN** 系統 SHALL 將狀態改為 `excluded` 並停止納入分析
- **AND** 移除該 workspace 的有效 assignment
- **AND** 不刪除核心專利、來源資料或模型 artifact

#### Scenario: 全庫 workspace 嘗試排除

- **WHEN** 呼叫者要求在全庫 workspace 建立或確認排除
- **THEN** 系統 SHALL 拒絕操作
- **AND** 全庫分析範圍不得因排除紀錄改變

### Requirement: CLU-011 Topic API 使用穩定識別與 Repository 邊界

系統 SHALL 以公開 `topic_key` 提供主題列表、合併建議、合併、合併歷史、解除合併與改名 API，不得把資料庫 numeric topic ID 暴露為公開識別；API 層須透過 `TopicRepository` protocol 與依賴注入存取正式 PostgreSQL adapter。

#### Scenario: 建立人工合併

- **WHEN** 呼叫者提供同一 workspace、同一 source field 下兩個不同且非空的 `topic_key`
- **THEN** 系統 SHALL 建立可追蹤的 merge 工作並回傳 `202`
- **AND** 不得接受少於、超過或重複的 topic keys

#### Scenario: 尚無相似度建議來源

- **WHEN** 系統沒有可用的相似度建議資料
- **THEN** merge suggestions SHALL 回傳空集合
- **AND** 不得捏造建議阻擋人工合併

#### Scenario: Repository 不可用

- **WHEN** 正式 Topic repository 無法服務
- **THEN** API SHALL 回傳可辨識的 `503`
- **AND** API 路由不得繞過 repository boundary 直接依賴 PostgreSQL driver
