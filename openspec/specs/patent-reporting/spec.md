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

### Requirement: RPT-009 法律狀態專用彙總與非阻塞背景刷新

系統 SHALL 僅在「專利狀態分析」中把詳細 `legal_status` 映射為彙總分類：`已申請`、`已公開`、`審查中` 對應 `pending`；`已核准` 對應 `alive`；`放棄`、`核駁`、`撤回`、`已失效`、`屆滿失效` 對應 `dead`；空值或無法辨識值對應 `unknown`。該對照 SHALL 由後端單一來源提供。

#### Scenario: 專利狀態分析使用一致分類
- **WHEN** 系統產生專利狀態分析資料或圖表
- **THEN** 每筆詳細狀態 SHALL 依唯一對照歸入正確分類
- **AND** 報表、API 與前端不得各自定義不同對照

#### Scenario: 儲存後只排程目前範圍的狀態分析
- **GIVEN** TW 狀態已成功提交
- **WHEN** 系統完成核心值與報表投影更新
- **THEN** 系統 SHALL 立即在背景排程目前選定 workspace 的專利狀態分析刷新
- **AND** SHALL NOT 因此排程其他 report key
- **AND** 前端 SHALL 留在原畫面並以非阻塞提示顯示刷新進度

#### Scenario: 背景刷新失敗不回滾狀態
- **GIVEN** 狀態目前值與歷程已成功提交
- **WHEN** 專利狀態分析 enqueue 或執行失敗
- **THEN** 已保存狀態與歷程 SHALL 保持不變
- **AND** 前端 SHALL 顯示刷新失敗與重試操作

#### Scenario: 重試只刷新專利狀態分析
- **WHEN** 使用者重試失敗的背景刷新
- **THEN** 系統 SHALL 重新排程相同 workspace 的專利狀態分析
- **AND** SHALL NOT 再次寫入 `legal_status` 或歷程

### Requirement: RPT-010 圖表單一來源輸出

系統 SHALL 為每張圖表只輸出**一個 SVG 檔**（既有原檔名、WEB 呈現尺寸）；
HTML 顯示與簡報轉換 SHALL 共用此同一來源，簡報端的字級適配由消費端執行，
引擎不得為特定輸出媒介預先產第二份尺寸版本。

#### Scenario: 新版本每張圖恰一檔

- **WHEN** 系統完成一次報表產製
- **THEN** 版本目錄內每張圖 SHALL 恰有一個 SVG（無 `.web` 中綴副本）
- **AND** SHALL 不產生 `profile_manifest.json`

#### Scenario: 舊版本相容顯示

- **GIVEN** 本需求生效前產製的版本（原檔名為簡報尺寸、另有 `.web.svg`）
- **WHEN** 網頁報表顯示該版本
- **THEN** SHALL 優先採用 `.web.svg`，缺檔時退回原檔——新舊版本皆正確顯示，
  不要求重產

### Requirement: RPT-011 申請人年度分布以跨度呈現

系統 SHALL 以「一列一家公司的投入期間（首件→末件）」呈現申請人年度分布，
並在條上標出**實際有申請的年份**、於條末標示總件數；全部呈現列數
SHALL 容納於單一圖面，不拆成第二張延伸圖。

⚠ 判準是「跨度本身是否帶有資訊」，不是稀疏度：主題與年份的分布
（主題演進）跨度接近全時間軸，SHALL 維持以量值大小呈現，不改跨度形式。

#### Scenario: 斷續投入不得畫成連續

- **GIVEN** 某申請人只在部分年份有申請（例如 2020、2022、2024）
- **WHEN** 系統產生申請人年度分布圖
- **THEN** 該列 SHALL 標出這些年份的實際落點
- **AND** SHALL NOT 使讀者將期間內未申請的年份誤讀為持續投入

#### Scenario: 單一年份仍可辨識

- **WHEN** 某申請人只有一個年份有申請
- **THEN** 該列 SHALL 以可見的標記呈現（不得為零寬度）
- **AND** 其寬度 SHALL 小於真正跨越多年的列，以免誤判為長期投入

#### Scenario: 量級資訊不因改用跨度而遺失

- **WHEN** 以跨度呈現（逐年件數不再直接可見）
- **THEN** 每列 SHALL 標示該申請人的總件數
- **AND** 逐年明細 SHALL 仍可由同章節的數據表取得

