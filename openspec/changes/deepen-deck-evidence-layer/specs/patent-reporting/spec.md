# patent-reporting（delta）

## ADDED Requirements

### Requirement: 外觀保護策略報表

系統 SHALL 提供 `design_protection_detail` 報表與對應 section，用於輸出外觀保護策略與技術專利交叉 evidence。

#### Scenario: 外觀判定包含 S 與 S1

- **WHEN** 報表資料列的 `document_kind` 為 `S` 或 `S1`
- **THEN** 系統 SHALL 將該列視為外觀設計
- **AND** SHALL NOT 只以 `patent_type='P'` 判斷技術專利

#### Scenario: 圖表與表格輸出

- **WHEN** 報表產生外觀保護策略 section
- **THEN** SHALL 輸出策略分布圖表
- **AND** SHALL 輸出外觀策略明細表
- **AND** SHALL 輸出技術專利與外觀設計交叉 evidence 表

#### Scenario: 不輸出 WIPS/PDF 連結

- **WHEN** 外觀策略列出代表外觀案或代表技術案
- **THEN** SHALL 使用本地 `patent_id` 或既有主附圖資產作為代表圖解析依據
- **AND** SHALL NOT 輸出 WIPS 連結
- **AND** SHALL NOT 輸出 PDF 連結

### Requirement: 外觀設計不得被靜默排除

分析母體 SHALL 包含外觀設計專利；技術分析軸若因主權項缺席而排除它們，
系統 SHALL 在報表正文顯示母體對帳，並為外觀設計提供獨立分析軸。

⚠ 2026-08-13 實測滑雪機 workspace：母體 55 件中 11 件為外觀設計，
被靜默排除後技術軸只剩 44 件，連帶漏掉 25% 的申請人（6 家只走外觀設計）。

#### Scenario: 母體對帳行

- **WHEN** 報表產生且母體含外觀設計
- **THEN** 正文 SHALL 顯示 `總數 = 技術 + 外觀` 的對帳行
- **AND** 三個數字 SHALL 由引擎產生，不得由 CLI 填寫

#### Scenario: 外觀設計判別基準

- **WHEN** 系統需要判定一筆專利是否為外觀設計
- **THEN** SHALL 以 `document_kind IN ('S','S1')` 判定
- **AND** SHALL NOT 以「無主權項」判定
- **AND** 判別邏輯 SHALL 只有單一定義處，引擎與報表共用

⚠ 兩者在 CN 資料上碰巧重合，一換到 US 就分開：id 452（`P/S1`、US、
洛迦諾 15-03、標題 `Mower`）有 58 字主權項，因為美國設計專利本來就有一條
claim。用「無主權項」判定會讓割草機的對帳寫成 `226 = 217 + 9`，
實際是 `226 = 216 + 10`——差 1 件，且不會有任何東西報錯。

#### Scenario: 外觀設計分析軸

- **WHEN** 母體含外觀設計且產生外觀設計軸
- **THEN** 主圖 SHALL 為「年度 × 申請人身分別」（只走外觀／技術+外觀／只走技術）
- **AND** SHALL 附只走外觀設計的申請人清單
- **AND** SHALL 標註法律狀態分布
- **AND** 洛迦諾分類 SHALL 只作異常值檢查，SHALL NOT 作為分析主軸

⚠ 洛迦諾不當主軸的依據：滑雪機 11 件中 91% 集中在 21-02，畫圖沒有訊息量；
審閱意見自身亦將其定位為異常值檢查。

### Requirement: 推定數字須揭露來源覆蓋率

凡欄位值係由推定得出（而非原始資料直接提供），報表 SHALL 在該數字旁
顯示 `來源欄位覆蓋率 N/M`。

#### Scenario: 覆蓋率跟著數字走

- **WHEN** 報表顯示一個推定得出的數字
- **THEN** 覆蓋率 SHALL 顯示在該數字旁，SHALL NOT 只放在頁尾或附錄

### Requirement: 圖表輸出軸 metadata

圖表 SHALL 隨產出提供其軸的 metadata（軸名、單位、資料範圍），
供下游驗收比對標題宣稱與圖上實際內容。

#### Scenario: 軸 metadata 隨圖產出

- **WHEN** 引擎產生任一圖表
- **THEN** SHALL 一併輸出該圖的軸 metadata
- **AND** 系統 SHALL NOT 以字串比對自動判定「標題變數是否存在於圖上」

⚠ 不自動判定的理由：同義詞（「家族數」對 `family_count`）字串比對會假警報，
維護同義詞表會漏，換個說法就繞過。該項留給目視迴圈，metadata 是讓目視有依據可比。
