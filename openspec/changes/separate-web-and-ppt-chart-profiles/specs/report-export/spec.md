## ADDED Requirements

### Requirement: EXP-018 使用者選圖解析為同 Identity 的 PPT Asset

系統 SHALL 將每張使用者選中的 web chart identity 解析為同一報表版本、同一 variant 與同一 chart identity 的 PPT profile asset，並把所有解析後圖片與 manifest 傳入規劃 CLI；CLI 不得自行增減、替換或重畫圖表。

#### Scenario: 使用者選取多張圖產生 PPT

- **WHEN** 使用者提交多個已驗證 chart identities
- **THEN** evidence manifest SHALL 為每個選項列出 web 與 PPT profile checksum lineage
- **AND** CLI 輸入 SHALL 包含每個選項對應的 PPT profile asset
- **AND** 最終 SlidePlan 與 PPT SHALL 使用全部選圖，不得漏圖或加圖

#### Scenario: PPT Profile Identity 不符

- **WHEN** 任一解析結果的 report version、variant、chart identity 或 dataset version 與使用者選項不一致
- **THEN** 匯出 SHALL fail loud
- **AND** 不建立可供核准的 SlidePlan 或 PPT

#### Scenario: CLI 嘗試指定未提供圖片

- **WHEN** SlidePlan 引用不在 evidence manifest 的 image identity
- **THEN** 組版閘門 SHALL 拒絕輸出
- **AND** 不得由檔案系統或資料庫自動搜尋替代圖片
