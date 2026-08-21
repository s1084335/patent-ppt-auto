## ADDED Requirements

### Requirement: WSP-013 初階篩選入口

瀏覽專利頁 SHALL 提供初階篩選的入口，並 SHALL 顯示待辦數量（關鍵字組數與待裁決件數），
使用者 SHALL NOT 需要進入該頁即可知道是否有待處理事項。

初階篩選的所有作業 SHALL 位於獨立頁面；瀏覽專利頁 SHALL NOT 承載關鍵字編輯、
比對詞確認或裁決操作。

#### Scenario: 入口顯示待辦數

- **WHEN** 使用者進入瀏覽專利頁且該 workspace 有待裁決項目
- **THEN** 入口 SHALL 顯示待裁決件數

#### Scenario: 無待辦時仍可進入

- **WHEN** 該 workspace 尚無關鍵字與待裁決項目
- **THEN** 入口 SHALL 仍可點擊進入以建立第一組關鍵字

## MODIFIED Requirements

### Requirement: WSP-003 分析排除與復原

系統 SHALL 允許人工排除、AI 待複核、確認排除、保留及復原；顯示成員與分析成員的差異必須可解釋。

確認排除後的專利 SHALL 進入封存狀態：SHALL NOT 出現在瀏覽專利清單，SHALL 於排除清單
可查見並可復原。

核心專利資料 SHALL NOT 因排除而立即刪除；僅在封存滿保留期（一年）且經明確的保留期
清理作業時，才 SHALL 允許刪除，且該刪除 SHALL 依 `patent-prefilter` 的保留期需求
執行引用清理與報表標記。

#### Scenario: 顯示公司名稱

- **WHEN** 專利具有已確認中文名或正規化名稱
- **THEN** 列表 SHALL 依公司治理優先序顯示名稱
- **AND** 詳情仍可取得來源原文

#### Scenario: 確認排除專利

- **WHEN** 使用者確認一筆專利不納入分析
- **THEN** 該專利 SHALL 從分析範圍排除
- **AND** 仍可在排除清單查見
- **AND** SHALL NOT 出現在瀏覽專利清單
- **AND** 保留期內不得從核心專利資料刪除

#### Scenario: 復原已排除專利

- **GIVEN** 排除時保存了原主題指派
- **WHEN** 使用者復原專利
- **THEN** 系統 SHALL 恢復 workspace 成員與可用的原指派資訊
- **AND** 該專利 SHALL 重新出現在瀏覽專利清單

#### Scenario: 保留期屆滿後允許刪除

- **GIVEN** 專利已封存滿一年
- **WHEN** 執行保留期清理作業
- **THEN** 系統 SHALL 允許刪除該專利的核心資料
- **AND** 刪除前 SHALL 提供 dry-run
