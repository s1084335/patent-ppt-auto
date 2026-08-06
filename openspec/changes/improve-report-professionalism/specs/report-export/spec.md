## ADDED Requirements

### Requirement: EXP-008 讀圖須知與論證元件

系統 SHALL 提供可由 goal-driven SlidePlan 安排的讀圖須知、母體、總覽、分類、主題、Key Player、代表專利、研發建議與附錄內容元件；不再要求所有報告採同一固定頁序。

#### Scenario: 本次選圖不需要固定章節

- **WHEN** 使用者選圖與最大目標不需要某一內容元件
- **THEN** SlidePlan MAY 不使用該元件
- **AND** 仍 SHALL 以實際選圖形成可追蹤論證鏈

### Requirement: EXP-009 Key Player 深入分析

系統 SHALL 在使用者選圖與最大目標需要 Key Player 分析時，以必要頁數呈現其布局、年度軌跡、共同申請／受讓與代表專利證據；不得固定要求三頁或在無相關選圖時硬加入。

#### Scenario: 選定追蹤公司

- **WHEN** 使用者提供追蹤公司清單
- **THEN** Key Player 頁 SHALL 只使用可追溯到該公司的專利與報表資料
- **AND** 公司無資料時 SHALL 清楚標示而非套用他公司內容

### Requirement: EXP-010 內容容量誠實

系統 SHALL 依實際版型容量限制文字與卡片，超出時縮減內容或分頁，不得截斷、遮蔽或把字縮到不可讀。

#### Scenario: Narrative 超過頁面容量

- **WHEN** 敘述超出目標版型容量
- **THEN** 系統 SHALL 依契約濃縮、減少項目或分頁
- **AND** 不得無聲裁切文字
