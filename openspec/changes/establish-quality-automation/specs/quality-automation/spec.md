## Purpose

定義專案可重現的版本、規格、靜態分析、測試與跨層資料契約自動守門，使交付證據可由乾淨環境重跑，並把本次新增問題與既有技術債分開呈現。

## ADDED Requirements

### Requirement: QUA-001 Python 版本單一來源

專案 SHALL 定義受支援 Python 版本，local uv、Docker 與 CI MUST 使用相容版本；版本漂移須由自動檢查現形。

#### Scenario: CI 使用不相容 Python
- **WHEN** workflow/runtime 版本不在專案支援範圍
- **THEN** version check SHALL 失敗並列出期望與實際版本

### Requirement: QUA-002 規格與程式品質守門

每次 change/PR SHALL 執行 OpenSpec strict validation、受影響範圍 lint、type check 與目標測試；已知歷史債與本次新增違規 MUST 分開輸出。

#### Scenario: 新增檔引入 lint 錯誤
- **WHEN** 本次新增／修改行觸發 blocking lint rule
- **THEN** CI SHALL 失敗，不得因全庫既有問題很多而忽略本次違規

### Requirement: QUA-003 測試不得連正式資料庫

自動測試 SHALL 使用 mock、隔離測試 DB 或明確 opt-in integration profile；任何指向正式 Supabase/production 的連線 MUST 在測試啟動前被拒絕或改寫。

#### Scenario: CI secret 誤帶正式 DATABASE_URL
- **WHEN** test process 偵測 production/Supabase 目標
- **THEN** 測試 SHALL fail-safe 或切換隔離目標，且不得寫入正式資料

### Requirement: QUA-004 跨層欄位契約可比較

系統 SHALL 由權威來源產生 API/report/export 可消費契約，並自動比較前端專利欄位、後端 response schema、report definitions 與 portable PPT 輸入；不得靠多份手工清單默默同步。

#### Scenario: 後端移除前端仍使用欄位
- **WHEN** 權威 API/輸出契約不再提供某欄而前端或 PPT 仍引用
- **THEN** contract check SHALL 失敗並指出 producer 與 consumer 差異

### Requirement: QUA-005 驗證產物可追溯

CI SHALL 保存測試摘要、lint/type/spec 結果與必要 coverage/diff metadata，讓驗收者能判斷測了什麼、沒測什麼與使用的 commit/config。

#### Scenario: 部分 integration test 被跳過
- **WHEN** 環境不具備 DB/browser/AI 前置而跳過測試
- **THEN** 結果 SHALL 明列 skipped 範圍與原因，不得用整體綠燈暗示已驗收該層
