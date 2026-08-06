## ADDED Requirements

### Requirement: RPT-009 單位與三層母體一致

系統 SHALL 全程只使用已定義的「件」與「群」單位，並以總專利、有效分析專利、正式技術群的三層漏斗揭露母體。

#### Scenario: 封面漏斗

- **WHEN** 產生報告封面
- **THEN** SHALL 顯示三層母體與各層排除原因
- **AND** 設計案不進技術分群時 SHALL 以備註揭露

### Requirement: RPT-010 技術與功效分頭論證

系統 SHALL 讓技術通道回答技術手段，功效通道回答效果／課題，並禁止產生技術×功效交叉矩陣。

#### Scenario: 兩通道均有結果

- **WHEN** 技術與功效主題都已 finalized
- **THEN** 報表 SHALL 分別呈現兩組主題與證據
- **AND** 不把兩套 labels 拼成未經驗證的交叉分類

### Requirement: RPT-011 報表組合先刪後改

系統 SHALL 先移除沒有獨立問題或與其他頁重複的報表，再改版既有報表，只有必要時才新增。

#### Scenario: 移除權人報表

- **WHEN** owner ranking/year matrix 被正式移除
- **THEN** registry、前端、report metadata、PPT 與測試 SHALL 同步刪除
- **AND** 其原本問題 SHALL 由 Key Player 或申請人／受讓分析承接並在設計中記錄

### Requirement: RPT-012 具名發現敘述

系統 SHALL 讓 narrative 指出具名對象、可核對數據、判讀意義與限制，並提供可供 goal-driven planner 引用的 report/variant/evidence identity；不以泛用模板句填滿固定數量。

#### Scenario: 資料不足

- **WHEN** 頁面沒有足夠證據形成發現
- **THEN** narrative SHALL 說明資料限制或少產一則
- **AND** 不捏造公司、主題或趨勢
