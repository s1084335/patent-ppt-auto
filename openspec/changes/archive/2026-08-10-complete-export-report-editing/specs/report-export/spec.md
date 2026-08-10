## ADDED Requirements

### Requirement: EXP-011 匯出歷史依 workspace 與版本隔離

系統 SHALL 列出目前 workspace 的 report/PPT artifacts、產生狀態、來源版本、分群世代與缺漏；無 workspace 標記或其他 workspace 的輸出不得混入。

#### Scenario: 切換 workspace
- **WHEN** 使用者從 workspace A 切到 B
- **THEN** 匯出歷史 SHALL 只顯示 B 的版本，且 A 的暫存選取不得沿用

### Requirement: EXP-012 編輯稿可持久化與追溯

系統 SHALL 將使用者編輯內容、版型覆寫與核准狀態分欄保存，重新整理後可依 `plan_id + slide_id` 讀回，並記錄 based-on version、updated_at 與更新者；頁碼變動不得把草稿套到另一張 slide。

#### Scenario: 編輯後重新整理
- **WHEN** 使用者保存某版編輯稿後重新載入匯出頁
- **THEN** 系統 SHALL 還原該版草稿，且不得覆蓋原始 report data 或 AI 原始輸出

### Requirement: EXP-013 單頁重產先比較後核准

系統 SHALL 允許從目前預覽的 `slide_id` 建立單頁候選，AI 只處理該 slide 原有選圖、evidence refs 與內容；不得改變本次選圖集合或跨 snapshot 補證據。候選與現行頁並列比較，只有使用者確認後才成為目標版本的核准覆寫。

#### Scenario: 使用者取消候選
- **WHEN** 使用者檢視單頁重產候選後選擇取消
- **THEN** 現有 PPT、編輯稿與核准覆寫 SHALL 完全不變

#### Scenario: 使用者確認候選
- **WHEN** 使用者指定目標版本並確認候選
- **THEN** 系統 SHALL 保存前一版追溯資訊並 deterministic rebuild 完整 PPTX，只有指定頁的核准內容改變
- **AND** 全部 selected chart identities 與非目標 slide evidence references SHALL 維持不變

### Requirement: EXP-014 HTML 與 PPT theme 同源

系統 SHALL 讓單頁 HTML 匯出消費與 PPT 相同的 theme token 與結構化內容，不得維護第三套硬編色彩、字級與間距規則。

#### Scenario: theme 版本更新
- **WHEN** 核准的 theme token 改變並重新匯出同一頁
- **THEN** HTML 與 PPT SHALL 反映同一版本 metadata，且不得由各自常數產生互相矛盾的風格
