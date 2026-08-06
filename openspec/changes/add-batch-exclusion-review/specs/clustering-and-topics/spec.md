## ADDED Requirements

### Requirement: CLU-010 批次裁決維持人工護欄

系統 SHALL 讓批次 keep 僅刪除 pending review、批次 confirm 才轉為 excluded 並移除對應 assignment；兩者都不得重跑分群或修改 model artifact，AI 仍不得直接執行 confirm。

#### Scenario: 批次確認排除
- **WHEN** 使用者人工確認多筆 pending 候選
- **THEN** 只有選取專利 SHALL 轉為 excluded 並移除 assignment，未選取與 artifact SHALL 不變
