## Purpose

定義跨 backend、worker 與部署環境的大型暫存物件傳遞契約，使資料庫不再承載檔案內容，同時保留完整性、重試、清理、供應商可替換與稽核能力。

## ADDED Requirements

### Requirement: OBJ-001 Object key 不可猜測且有範圍

系統 SHALL 為每次上傳建立不可猜測、不得穿越 bucket prefix 的 object key，並將 workspace/job 意圖保存在受權限控制的 metadata，而非公開 URL。

#### Scenario: 建立匯入暫存物件
- **WHEN** 使用者上傳允許格式的檔案
- **THEN** 系統 SHALL 建立唯一 object key，且不同 workspace 的 key 不得互相覆蓋

### Requirement: OBJ-002 串流與完整性

系統 SHALL 串流寫入與讀出物件，保存 byte size 與 SHA-256；worker 在交給 importer 前 MUST 驗證下載內容與預期值一致。

#### Scenario: 下載內容遭截斷
- **WHEN** 實際大小或 SHA-256 與 job metadata 不一致
- **THEN** 系統 SHALL 拒絕匯入、標記可理解錯誤且不得把損壞檔案送入 importer

### Requirement: OBJ-003 終結態生命週期

系統 SHALL 在 queued、running 與可重試失敗期間保留物件；在 succeeded、final failed 或 cancelled 後冪等刪除，刪除失敗須可由 orphan cleanup 補償。

#### Scenario: 可重試工作第一次失敗
- **WHEN** job 尚有 retry 且進入下一次 queued
- **THEN** 系統 SHALL 保留同一物件供重試，不得提前刪除

#### Scenario: 終結態重複清理
- **WHEN** 同一物件被成功路徑與補償流程重複刪除
- **THEN** 第二次清理 SHALL 視為冪等成功並留下 audit 結果

### Requirement: OBJ-004 供應商設定與 secret

系統 SHALL 以 endpoint、bucket、region/compatibility 與 credential 設定連接 S3 相容服務；secret MUST 不進 repo、job payload、一般 API response 或未遮罩 log。

#### Scenario: 設定不完整
- **WHEN** object-store mode 已啟用但必要設定缺失
- **THEN** 系統 SHALL 在接收上傳前 fail-fast，不得回退到部分寫 DB、部分寫 object store
