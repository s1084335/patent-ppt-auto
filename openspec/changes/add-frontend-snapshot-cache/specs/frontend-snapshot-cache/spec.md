## Purpose

讓瀏覽與分類畫面可先使用最近成功資料、再背景刷新，並在斷線或刷新失敗時保留可理解、可追溯的舊狀態。

## ADDED Requirements

### Requirement: FSC-001 Snapshot 具有類型與版本

系統 SHALL 以 workspace、snapshot type、scope identity、資料版本與產生時間識別 snapshot。

#### Scenario: 讀取最近成功 snapshot

- **WHEN** 前端開啟有既存 snapshot 的資料區
- **THEN** API SHALL 回傳最近成功版本與 metadata
- **AND** GET 不隱含執行昂貴 refresh

### Requirement: FSC-002 Snapshot-first 刷新

系統 SHALL 先顯示 snapshot，再於背景取得新資料；背景刷新不得先清空舊表格。

#### Scenario: 背景刷新成功

- **GIVEN** 畫面已顯示舊 snapshot
- **WHEN** 新查詢成功
- **THEN** 對應區塊 SHALL 原子切換為新版本
- **AND** 更新資料時間

#### Scenario: 背景刷新失敗

- **GIVEN** 畫面已有舊 snapshot
- **WHEN** backend 查詢失敗
- **THEN** 舊內容 SHALL 保留
- **AND** 畫面 SHALL 標示 stale 與可重試狀態

### Requirement: FSC-003 無 snapshot 空狀態

系統 SHALL 在首次使用且 backend 不可用時顯示可理解的空狀態，不得永久卡在 loading。

#### Scenario: 首次離線

- **WHEN** 找不到 snapshot 且即時查詢失敗
- **THEN** 畫面 SHALL 顯示錯誤與重試操作

### Requirement: FSC-004 區塊 identity 與互動狀態

系統 SHALL 只重畫受影響區塊，並保存不屬於資料內容的展開、收合、選取、編輯與版本選擇狀態。

#### Scenario: 剔除列表中的另一筆專利

- **GIVEN** 使用者已展開一筆專利詳情
- **WHEN** 剔除另一筆專利並刷新列表
- **THEN** 原展開狀態 SHALL 保留

### Requirement: FSC-005 寫入影響 mapping 唯一來源

系統 SHALL 以單一 mapping 定義每個寫入操作成功後需要失效或刷新的區塊。

#### Scenario: 新分群完成

- **WHEN** 分群 finalize 成功
- **THEN** 主題列表、候選／正式狀態與相關專利區塊 SHALL 依 mapping 更新
- **AND** 不重畫無關的進階操作收合區

