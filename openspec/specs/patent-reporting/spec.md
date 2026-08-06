# Patent Reporting Specification

## Purpose

定義報表 registry、SQL 聚合、分群分析、圖表、敘述、版本與 artifact 的現行契約。

## Requirements

### Requirement: RPT-001 報表定義單一 registry

系統 SHALL 由 `REPORT_DEFINITIONS` 定義報表名稱、來源、欄位、排序、版面、篩選能力與資料需求；API 與前端可用清單必須受一致性測試保護。

#### Scenario: 未知報表名稱

- **WHEN** 使用者要求 registry 中不存在的 report key
- **THEN** API SHALL 回傳驗證錯誤
- **AND** 不建立報表工作

### Requirement: RPT-002 白名單 SQL 聚合

系統 SHALL 只允許 registry 宣告的 table、column、aggregate、filter 與排序組合進入動態 SQL，識別字必須安全引用。

#### Scenario: 未允許的 filter

- **WHEN** 請求含不在報表白名單的 filter column
- **THEN** 系統 SHALL 拒絕請求

### Requirement: RPT-003 正確母體與共同申請口徑

系統 SHALL 以一專利一列 base 計算一般母體；申請人排名、申請人年度矩陣與公司×國家矩陣使用申請人展開 view，並標示共同申請會使加總大於專利件數。

#### Scenario: 共同申請人排名

- **GIVEN** 一件專利具名兩位申請人
- **WHEN** 產生申請人排名
- **THEN** 兩位申請人 SHALL 各自取得一件貢獻
- **AND** 輸出 SHALL 帶有共同申請母體說明

### Requirement: RPT-004 專利種類與分類口徑

系統 SHALL 區分發明、新型與設計等 document kind，IPC/CPC 報表只統計符合分類格式的值，避免把洛迦諾分類混入。

#### Scenario: 設計案分類值

- **WHEN** IPC 欄出現純數字洛迦諾代碼
- **THEN** IPC/CPC 分布 SHALL 排除該值

### Requirement: RPT-005 SQL 與分群報表並存

系統 SHALL 支援 aggregate/detail SQL 報表與 finalized topic state 衍生的 cluster 報表；cluster 報表不得假裝能由一般單表 SQL 執行。

#### Scenario: 缺少 workspace 分群範圍

- **WHEN** 請求 cluster 報表但沒有可用 finalized topic data
- **THEN** 系統 SHALL 顯示 skipped reason 或缺資料狀態
- **AND** 不捏造空主題結果

### Requirement: RPT-006 版本化報表輸出

系統 SHALL 為每次報表產製建立不可混淆的 version/run directory，保存 report data、圖表、section metadata、HTML 與版本歸屬。

#### Scenario: 同一 workspace 重跑報表

- **WHEN** 使用者再次產生報表
- **THEN** 新版本 SHALL 不覆蓋舊版本
- **AND** API 可列出與讀取指定版本

### Requirement: RPT-007 AI 敘述可追溯

系統 SHALL 以 report key 與 variant key 對應敘述，保存 prompt/version freshness；過期或缺漏敘述必須現形，不得套用到錯頁。

#### Scenario: 報表資料已更新

- **WHEN** narrative 來源版本與目前 report data 不一致
- **THEN** UI／輸出 SHALL 標示過期或要求重產
- **AND** 不把舊敘述當成目前分析

### Requirement: RPT-008 重分類後報表鏈完整

系統 SHALL 在欄位重分類後成功刷新 report base 與 applicant expanded view，所有依賴欄位的報表須明確產出或回報可解釋錯誤。

#### Scenario: 執行最小 DB smoke

- **WHEN** 0045/0046 套用後刷新 derived
- **THEN** `patent_type`、`document_kind`、展開申請人與至少一個 0046 搬移欄位 SHALL 存在且有代表性非空值
- **AND** `applicant_ranking` 與 `ipc_main_distribution` SHALL 可執行

#### Scenario: 選定報表漏產

- **WHEN** 使用者選定一個 report key 但產製流程沒有輸出
- **THEN** 結果 SHALL 列出 missing report 與原因
- **AND** 不得以整體 succeeded 隱藏漏產
