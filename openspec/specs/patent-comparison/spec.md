# Patent Comparison Specification

## Purpose

定義目前已實作的案件建立、標的與比對專利來源、權利要求理解、人工閘門、候選證據與逐要素資料契約，確保 AI 產生的理解與證據可版本化、可人工修正，且未核准內容不會直接進入後續比對結論。

## Requirements

### Requirement: COM-001 案件狀態可追蹤

系統 SHALL 以唯一 job/case ID 保存案件狀態、來源、理解版本、人工核准與後續分析資料。

#### Scenario: 建立案件

- **WHEN** 使用者提交合法案件標題與說明
- **THEN** 系統 SHALL 建立可查詢的 comparison job
- **AND** 回傳後續 API 使用的唯一 ID

### Requirement: COM-002 標的來源明確

系統 SHALL 支援以既有專利或使用者提供內容建立標的，並保留來源 identity；不得把不同來源無聲混合。

#### Scenario: 既有專利作為標的

- **WHEN** 使用者指定庫內專利號
- **THEN** 系統 SHALL 解析成可追溯 patent identity 與內容

### Requirement: COM-003 比對專利來源明確

系統 SHALL 支援庫內候選搜尋與使用者提供的比對專利內容，保存被選定的 reference patents。

#### Scenario: 相似候選搜尋

- **WHEN** 使用者要求庫內 reference candidates
- **THEN** 系統 SHALL 回傳具 patent identity 與相似證據的候選
- **AND** 不自動替使用者定案最終比對集合

### Requirement: COM-004 權利要求結構化理解

系統 SHALL 解析權利要求條次、獨立／附屬關係與引用，產生版本化 understanding payload。

#### Scenario: 產生新理解版本

- **WHEN** 來源權利要求更新或重新解析
- **THEN** 系統 SHALL 建立新 understanding version
- **AND** 保留前一版本供追溯

### Requirement: COM-005 人工核准閘門

系統 SHALL 要求使用者核准目前 understanding version 後，才可進入依賴該理解的逐要素分析。

#### Scenario: 未核准理解

- **WHEN** 呼叫者要求逐要素分析但目前版本未核准
- **THEN** 系統 SHALL 拒絕或維持等待狀態

### Requirement: COM-006 證據與判斷分離

系統 SHALL 保存 claim element、對應說明書／圖式證據與分析資料，並將工具輸出定位為輔助分析，不宣稱法律結論。

#### Scenario: 缺少證據

- **WHEN** 某要素找不到可靠來源內容
- **THEN** 輸出 SHALL 標示缺證或未知
- **AND** 不得自行補造支持文字

### Requirement: COM-007 未完成交付不得冒充正式功能

系統 SHALL 不把尚未完成驗收的最終 verdict、法律結論或正式案件比對 PDF 宣告為目前交付能力。

#### Scenario: 查詢目前能力

- **WHEN** 使用者檢視案件比對輸出
- **THEN** UI/API SHALL 清楚區分已實作的理解／證據資料與未交付的最終法律輸出
