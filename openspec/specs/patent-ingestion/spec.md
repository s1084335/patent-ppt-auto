# Patent Ingestion Specification

## Purpose

定義 WIPS 專利檔案從上傳、解析、正規化、去重到核心資料與後續工作的現行契約，涵蓋來源追蹤、識別碼合併、原始值保存與錯誤輸出，並維持 importer、schema、derived 及報表之間的欄位一致性。

## Requirements

### Requirement: ING-001 匯入格式與入口白名單

系統 SHALL 允許 Web 匯入 `.xlsx`、`.csv`、`.txt`、`.xml`，並拒絕路徑穿越、空檔名、非白名單副檔名與超過大小上限的請求。

#### Scenario: 上傳不支援格式

- **WHEN** 使用者上傳副檔名不在 Web 白名單的檔案
- **THEN** API SHALL 拒絕請求
- **AND** 不建立匯入工作

### Requirement: ING-002 原始資料可追溯

系統 SHALL 以檔案 hash、來源列與 `patent_sources` 關聯保存來源追溯，且相同原始檔不得無聲重複匯入。

#### Scenario: 重複檔案

- **GIVEN** 相同檔案 hash 已完成匯入
- **WHEN** 再次匯入同一檔案
- **THEN** 系統 SHALL 回報重複狀態
- **AND** 不重複新增 raw records

### Requirement: ING-003 多識別碼去重

系統 SHALL 保留授權號、審查公開號、未審查公開號與申請號，並依授權號、審查公開號、未審查公開號、申請號的順序尋找既有專利。

#### Scenario: 次要識別碼命中既有專利

- **GIVEN** 新來源的授權號為空，但公開號命中既有專利
- **WHEN** 匯入該來源列
- **THEN** 系統 SHALL 更新同一筆專利
- **AND** 不因授權號為空另建重複專利

### Requirement: ING-004 欄位依用途落層

系統 SHALL 將分析、分群、查詢、顯示或報表會使用的專利欄位存入 `patents` 或 `patent_people`，只將未被上述流程使用的殘餘欄位存入 `patent_attributes`。

#### Scenario: 核心欄位更新

- **GIVEN** 來源檔包含目前分析會使用的欄位
- **WHEN** 匯入完成
- **THEN** 欄位值 SHALL 可由核心表直接取得
- **AND** 不在 attributes 保存第二份相同定義

### Requirement: ING-005 人員與代表圖保存

系統 SHALL 保存申請人、專利權人、發明人等人員資料，並在來源含嵌入圖時保存圖像與代表圖快取。

#### Scenario: 同一專利有多位申請人

- **WHEN** 匯入列含多位申請人
- **THEN** `patent_people` SHALL 保留可分辨的人員關係
- **AND** 後續共同申請分析可展開各具名申請人

### Requirement: ING-006 匯入後續工作

系統 SHALL 在匯入成功後提供本次涉及的專利 ID，讓 workspace、derived refresh、embedding 與後續 AI 工作能以明確範圍接續。

#### Scenario: 匯入零新增但命中既有專利

- **WHEN** 匯入只更新或命中既有專利
- **THEN** 結果 SHALL 仍回傳受影響專利 ID
- **AND** 後續工作不得只依新增筆數判斷是否執行

### Requirement: ING-010 成對保存文獻階段附圖

系統 SHALL 以 `(patent_id, document_kind)` 保存同一專利不同文獻階段的附圖，並依明確的文獻階段優先序更新 `patents.main_figure` 代表圖快取，不得依 Excel 列序或字母排序決定最新版。

#### Scenario: 同一專利同時匯入 A 與 B 階段附圖

- **WHEN** 匯入資料包含同一專利的 A 與 B 階段附圖
- **THEN** `patent_figures` SHALL 同時保留兩張圖
- **AND** `main_figure` SHALL 依文獻階段優先序指向較後階段的圖

#### Scenario: 文獻階段未知或缺少

- **WHEN** 附圖沒有可辨識的 document kind
- **THEN** 系統 SHALL 保守保存圖像並記錄警告
- **AND** 未知階段不得覆蓋已知較高優先序的代表圖

#### Scenario: 重複匯入相同附圖

- **WHEN** 相同專利與 document kind 再次匯入
- **THEN** 系統 SHALL 批次 upsert 現有資料
- **AND** 不建立重複 pair
- **AND** 不以逐筆寫入造成 N+1
