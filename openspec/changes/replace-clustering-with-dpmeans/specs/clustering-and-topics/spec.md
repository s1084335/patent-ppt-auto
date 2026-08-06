## MODIFIED Requirements

### Requirement: CLU-004 現行增量分群

系統 SHALL 使用既有正式 artifact 對新增文件執行 Cosine Online DP-Means 增量流程，保留舊 assignments，並在距離超過資料驅動 lambda 時建立新主題與新 run。

#### Scenario: 增量後查詢舊專利

- **WHEN** 新增文件完成增量分群
- **THEN** 舊專利 SHALL 仍可由 run chain 取得主題歸屬
- **AND** 新 run 不得讓舊 assignments 從 UI 或報表消失

#### Scenario: 新資料形成遠離既有中心的群

- **GIVEN** 新文件與所有既有中心的 cosine distance 均超過 lambda
- **WHEN** 執行增量分群
- **THEN** 系統 SHALL 建立新正式主題
- **AND** 為新主題排入 `ai:topic_label`

## ADDED Requirements

### Requirement: CLU-008 Lambda 資料驅動且可重現

系統 SHALL 由校準資料推導 lambda，保存推導方法與值；相同輸入、模型與設定應得到相同 lambda。

#### Scenario: 使用者執行自動分群

- **WHEN** 系統校準 DP-Means
- **THEN** 不要求使用者輸入 lambda
- **AND** run metadata SHALL 保存實際值與推導版本

### Requirement: CLU-009 PCA 後重新正規化

系統 SHALL 保留 PCA 降維，並在 cosine clustering 前對降維向量重新做 L2 normalization。

#### Scenario: 向量進入 DP-Means

- **WHEN** PCA 產生降維向量
- **THEN** 每個有效向量 SHALL 在距離計算前完成 L2 normalization

