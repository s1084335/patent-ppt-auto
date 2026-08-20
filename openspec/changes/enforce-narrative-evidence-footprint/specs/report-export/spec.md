## ADDED Requirements

### Requirement: EXP-008 解讀必須留下取證足跡

`ai:narrative` 產出的 `narratives.json` SHALL 在頂層提供 `evidence` 物件，記錄每張報表用來
支撐深入描述的取證來源。缺少、為空或本次查詢數為零時，系統 SHALL 產生可辨識的契約警告，
但 SHALL NOT 使工作失敗。

⚠ `evidence` 對組版端維持 additive：組版與 PPT 端 SHALL 忽略此鍵，既有 `narratives.json`
SHALL NOT 因缺少此鍵而無法顯示。

#### Scenario: 有取證且足跡完整

- **GIVEN** 一次解讀在寫作前查過資料庫
- **WHEN** 解讀完成並寫出 `narratives.json`
- **THEN** 頂層 SHALL 有 `evidence`，其鍵為 report key
- **AND** 每筆項目 SHALL 含 `claim`、`queried` 與 `patent_ids`
- **AND** SHALL NOT 產生未取證警告

#### Scenario: 完全未取證

- **GIVEN** 一次解讀從頭到尾沒有呼叫任何取證工具
- **WHEN** 解讀完成
- **THEN** 契約警告 SHALL 指出本次未取證，並標示查詢次數為零
- **AND** 工作狀態 SHALL 仍為 `succeeded`
- **AND** 已產出的解讀內容 SHALL 保留可用

#### Scenario: evidence 存在但為空

- **GIVEN** `narratives.json` 頂層有 `evidence` 但其值為空物件
- **WHEN** 契約驗證執行
- **THEN** SHALL 與缺少 `evidence` 同等處理，產生契約警告

#### Scenario: 組版端不受影響

- **GIVEN** 一份不含 `evidence` 的既有 `narratives.json`
- **WHEN** 報表重新組版
- **THEN** 組版 SHALL 正常完成
- **AND** SHALL NOT 因缺鍵而報錯或遺漏頁面

### Requirement: EXP-009 取證稽核必須隨工作結果落庫

`ai:narrative` 的工作結果 SHALL 包含 `query_audit` 與 `query_count`，並隨結果寫入
`app_layer.workflow_outputs`，使「這次查了幾次、查了什麼」不依賴執行機器上的任何檔案。

⚠ 稽核 SHALL 只記查詢行為（工具、範圍、回傳列數、是否截斷、是否失敗），
SHALL NOT 記錄查詢回傳的專利內容——稽核不得變成資料副本。

#### Scenario: 有查詢

- **GIVEN** 一次解讀呼叫了取證工具
- **WHEN** 工作完成並回存結果
- **THEN** `job_result` SHALL 含 `query_audit` 陣列與對應的 `query_count`
- **AND** 兩者 SHALL 可由 `workflow_outputs` 讀回

#### Scenario: 零查詢不得省略欄位

- **GIVEN** 一次解讀完全沒有呼叫取證工具
- **WHEN** 工作完成並回存結果
- **THEN** `query_count` SHALL 為 `0`
- **AND** `query_audit` SHALL 為空陣列
- **AND** 兩個欄位 SHALL 存在，SHALL NOT 因為值為空而被省略

#### Scenario: 稽核讀取失敗不得拖垮工作

- **GIVEN** 稽核紀錄因故讀不回來
- **WHEN** 工作完成
- **THEN** 工作 SHALL 仍依解讀本身的結果判定成敗
- **AND** `query_count` SHALL 為 `0`，使稽核缺失本身現形

### Requirement: EXP-010 解讀契約警告必須對使用者可見

解讀產生的契約警告 SHALL 隨工作結果落庫，並 SHALL 在前端 AI 任務介面顯示；
違規 SHALL NOT 只存在於執行期記憶體或伺服器日誌。

#### Scenario: 有警告

- **GIVEN** 一次解讀產生了契約警告（未取證、漏產變體或三件套超限）
- **WHEN** 使用者在前端檢視該 AI 任務
- **THEN** `job_result` SHALL 含 `contract_warnings`
- **AND** 前端 SHALL 顯示這些警告文字
- **AND** 工作狀態 SHALL 仍顯示為成功，兩者並存不互相取代

#### Scenario: 無警告

- **GIVEN** 一次解讀通過全部契約檢查
- **WHEN** 使用者在前端檢視該 AI 任務
- **THEN** `contract_warnings` SHALL 為空陣列
- **AND** 前端 SHALL NOT 顯示警告區塊
