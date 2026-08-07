## ADDED Requirements

### Requirement: CLU-013 補分候選判定唯一定義處

系統 SHALL 以單一函式判定某通道的補分候選：該通道無 embeddings（不在分群輸入母體）且 `document_kind != 'S'`。設計案判定 MUST 使用 `document_kind`，MUST NOT 使用 `patent_type`。此判定 SHALL 為技術通道、功效通道與任何日後通道共用，不得在呼叫端另寫條件。

#### Scenario: B 組專利入選
- **GIVEN** 一件 TW 專利無 `獨立項` 值（技術通道無 embeddings）且 `document_kind='M'`
- **WHEN** 系統計算技術通道補分候選
- **THEN** 該專利 SHALL 在候選清單內

#### Scenario: 設計案排除
- **GIVEN** 一件專利 `document_kind='S'`
- **WHEN** 系統計算任一通道補分候選
- **THEN** 該專利 SHALL NOT 出現在候選清單
- **AND** 排除不得以 `patent_type` 判定

### Requirement: CLU-014 AI 建議屬敘述型輔助，不得直接成為正式指派

補分 AI 產出（建議主題＋一句理由）SHALL 隨 job result 落 `app_layer.workflow_outputs`（output_type='job_result:ai:topic_backfill'，含 prompt_version／ai_model），MUST NOT 直接寫入 `topic_assignments`。（⚠ 2026-08-07 實測回寫：原規格寫 analysis_outputs，該表實為 legacy_0021 空表、從未使用——現行通用回存＝workflow_outputs job result 通道。）機制 SHALL 為通道通用設計；本 change 僅接技術通道，AI 輸入 SHALL 為文獻備註三級 fallback 文本（`PATENT_NOTE_SOURCE_COLUMNS`），MUST NOT 回落其他欄位。（功效通道接線於功效通道改版輪實作：輸入＝`解决课题 摘要`、不得回落他欄、輸入為空以「無可補分輸入」現形——定案記於 proposal，屆時引用。）建議主題 SHALL 限定為該通道現有主題清單中的 key；AI 回傳清單外主題時該筆 SHALL 標記為無效建議並現形，不得靜默丟棄或自創主題。

#### Scenario: 建議產出落敘述型通道
- **GIVEN** 技術通道有 9 件補分候選且已有主題清單
- **WHEN** 補分 AI 任務完成
- **THEN** 最新 job result（workflow_outputs）SHALL 含 9 筆建議（主題 key＋理由）
- **AND** `topic_assignments` SHALL 無任何新增

#### Scenario: 清單外主題現形
- **WHEN** AI 回傳的建議主題不在現有主題清單
- **THEN** 該筆 SHALL 以無效建議呈現於核准清單（不可核准、附原因）

### Requirement: CLU-015 批次核准後由確定性程式寫入正式指派

使用者 SHALL 能在分類區檢視補分建議清單（專利、建議主題、理由）、逐筆改選主題、並一鍵批次核准。核准 SHALL 由確定性程式寫入 `topic_assignments` 並帶來源標記（區別於幾何分群指派）；核准前建議 MUST NOT 進入任何報表統計。寫入 MUST NOT 觸發重新分群、重算 embeddings 或變動既有指派。

#### Scenario: 一鍵全收
- **GIVEN** 9 筆有效建議
- **WHEN** 使用者按「全部核准」
- **THEN** `topic_assignments` SHALL 新增 9 筆帶來源標記的指派
- **AND** 既有幾何指派 SHALL 全數不變
- **AND** 不得建立任何 clustering／embedding 工作

#### Scenario: 逐筆改選後核准
- **GIVEN** 使用者把其中一筆建議主題改為另一個現有主題
- **WHEN** 批次核准
- **THEN** 寫入值 SHALL 為使用者改選的主題

#### Scenario: 核准前不進統計
- **GIVEN** 建議已產出但尚未核准
- **WHEN** 產出任何報表
- **THEN** 該批專利 SHALL NOT 計入該通道主題統計

### Requirement: CLU-016 補分指派進入報表母體

核准寫入後，該通道報表母體 SHALL 自動含補分件（技術通道 35 → 44），與功效通道及 IPC 分析同母體；主題統計、機會矩陣等下游 SHALL 一體反映，不需另行重跑分群。母體註記 SHALL 能區分幾何指派與人工核准之 AI 建議件數。

#### Scenario: 報表母體反映
- **GIVEN** 9 筆補分指派已核准寫入
- **WHEN** 重產技術通道相關報表
- **THEN** 母體 SHALL 為 44 件且含 TW 案
- **AND** 註記 SHALL 呈現「其中 N 件為 AI 建議、人工核准」
