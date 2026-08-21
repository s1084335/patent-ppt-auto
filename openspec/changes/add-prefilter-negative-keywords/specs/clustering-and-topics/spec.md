## ADDED Requirements

### Requirement: CLU-017 兩條剔除線共用同一裁決機制

系統 SHALL 允許多個候選來源產生剔除候選（分群後的 c-TF-IDF 判讀線、分群前的負面
關鍵字初階篩選線），但它們 SHALL 共用同一份排除紀錄、同一組狀態與同一個復原機制。

系統 SHALL NOT 為不同候選來源建立平行的排除資料或平行的裁決流程。

每筆候選 SHALL 記錄其來源，使人工裁決時可分辨它是哪一條線挑出來的。

#### Scenario: 兩線候選落在同一份紀錄

- **WHEN** 同一 workspace 先後由初階篩選與 c-TF-IDF 線各產生候選
- **THEN** 兩者 SHALL 出現在同一份待裁決紀錄中
- **AND** 各自的來源 SHALL 可辨識

#### Scenario: 同一專利被兩線命中

- **GIVEN** 某專利已由一條線列為待裁決
- **WHEN** 另一條線也命中該專利
- **THEN** 系統 SHALL NOT 產生重複的待裁決項目
- **AND** 該專利的候選來源 SHALL 同時記錄兩者

#### Scenario: 已裁決者不被重新列入

- **GIVEN** 某專利已由使用者裁決為保留
- **WHEN** 任一條線再次命中該專利
- **THEN** 系統 SHALL NOT 將其重新列為待裁決

## MODIFIED Requirements

### Requirement: CLU-007 排除不刪核心資料

系統 SHALL 將 AI 不相干判讀保存為 pending review，只有人工確認後才從分析範圍排除，且可復原。

核心專利、來源資料與模型 artifact SHALL NOT 因排除而刪除。僅在專利封存滿保留期
（一年）且執行明確的保留期清理作業時，才 SHALL 允許刪除核心專利資料；該刪除
SHALL 依 `patent-prefilter` 的保留期需求執行，並 SHALL NOT 作為排除操作的一部分自動發生。

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

#### Scenario: 排除本身不觸發刪除

- **WHEN** 使用者確認排除
- **THEN** 系統 SHALL NOT 於該操作中刪除任何核心專利資料
- **AND** 刪除只 SHALL 由保留期清理作業執行
