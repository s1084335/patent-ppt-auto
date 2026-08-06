## ADDED Requirements

### Requirement: RPT-015 同一圖表支援可驗證的 Web 與 PPT Profile

系統 SHALL 以相同 chart identity、dataset version、資料列、排序、色彩語意與版面邏輯產生 `web` 與 `ppt` rendering profile；兩者只得依目標媒介調整畫布尺寸、DPI、字級與必要邊距。

#### Scenario: 同一圖表產生兩種 Profile

- **WHEN** 報表版本產生可供網頁選取與 PPT 使用的圖表
- **THEN** 兩個 artifact SHALL 具有相同 report、variant 與 chart identity
- **AND** manifest SHALL 記錄各自 profile、dataset version 與 checksum
- **AND** 資料、排序與色彩語意不得因 profile 不同而改變

#### Scenario: 其中一種 Profile 產生失敗

- **WHEN** web 或 PPT profile 缺少、損壞或 checksum 不符
- **THEN** 該 chart identity SHALL 被標示為不完整
- **AND** 系統不得以另一張任意舊圖或不同 identity 圖片代替

#### Scenario: 舊報表版本只有單一圖檔

- **WHEN** 使用者選擇尚未產生雙 profile 的舊報表版本
- **THEN** 系統 SHALL 明確標示需要重產
- **AND** 不得把舊單 profile 靜默視為已驗證的 PPT profile
