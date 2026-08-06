## ADDED Requirements

### Requirement: ING-009 匯入檔由 object key 交接

系統 SHALL 在 object-store mode 將上傳內容串流存入物件儲存，job 只保存 object key、原檔名、大小、hash 與匯入意圖；匯入結果、格式驗證、identifier 去重與後續工作契約維持不變。

#### Scenario: 大型 WIPS 檔成功匯入
- **WHEN** backend 與 worker 位於不同檔案系統且收到大型允許格式檔案
- **THEN** worker SHALL 由 object key 取得內容並完成既有匯入流程，PostgreSQL 不得保存該檔 bytea

#### Scenario: 過渡期舊工作
- **WHEN** queued/running job 仍只帶既有 `blob_id`
- **THEN** 過渡版本 SHALL 可完成該工作，且不得因新 object-key 路徑上線而遺失內容
