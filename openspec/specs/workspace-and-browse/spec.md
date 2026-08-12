# Workspace and Browse Specification

## Purpose

定義全庫與自訂 workspace、專利瀏覽、排除／復原及 workspace 文件的現行行為，確保查詢、分群、報表與版本資料遵守同一範圍及權限，且顯示值、來源字面與使用者操作都能被正確追溯。
## Requirements
### Requirement: WSP-001 Workspace 範圍

系統 SHALL 支援全庫 workspace 與使用者建立的專利集合，所有分群、報表與版本資料須保留 workspace 歸屬。

#### Scenario: 建立自訂 workspace

- **GIVEN** 一組有效 patent IDs
- **WHEN** 使用者建立 workspace
- **THEN** 系統 SHALL 保存名稱與成員範圍
- **AND** 後續工作只處理該 workspace 的有效成員

### Requirement: WSP-002 專利搜尋與列表

系統 SHALL 提供專利搜尋、分頁列表、顯示欄位與代表圖讀取，並保留正規化顯示值與原始字面供追溯。

#### Scenario: 顯示公司名稱

- **WHEN** 專利具有已確認中文名或正規化名稱
- **THEN** 列表 SHALL 依公司治理優先序顯示名稱
- **AND** 詳情仍可取得來源原文

### Requirement: WSP-003 分析排除與復原

系統 SHALL 允許人工排除、AI 待複核、確認排除、保留及復原；顯示成員與分析成員的差異必須可解釋。

#### Scenario: 確認排除專利

- **WHEN** 使用者確認一筆專利不納入分析
- **THEN** 該專利 SHALL 從分析範圍排除
- **AND** 仍可在排除清單查見
- **AND** 不得直接從核心專利資料刪除

#### Scenario: 復原已排除專利

- **GIVEN** 排除時保存了原主題指派
- **WHEN** 使用者復原專利
- **THEN** 系統 SHALL 恢復 workspace 成員與可用的原指派資訊

### Requirement: WSP-004 Workspace 組合

系統 SHALL 能由既有 workspace 組合新 workspace，並保存來源關係與可追溯的成員集合。

#### Scenario: 合併多個來源 workspace

- **WHEN** 使用者選擇多個來源建立組合 workspace
- **THEN** 成員 SHALL 依專利 ID 去重
- **AND** 來源 workspace 關係 SHALL 被保存

### Requirement: WSP-005 Workspace 文件

系統 SHALL 支援 workspace 文件上傳、列出、讀取與刪除，並驗證文件確實屬於指定 workspace。

#### Scenario: 跨 workspace 讀取文件

- **WHEN** 呼叫者用另一 workspace ID 讀取文件
- **THEN** 系統 SHALL 拒絕或回傳不存在

### Requirement: WSP-007 自動刷新保留互動狀態

系統 SHALL 在背景工作成功後刷新可見資料，但保留展開、收合、選定 workspace/topic、報表版本與編輯模式等非資料狀態。

#### Scenario: 文獻備註完成

- **GIVEN** 使用者停留在含該專利的瀏覽表格
- **WHEN** `ai:patent_note` 成功完成
- **THEN** 文獻備註 SHALL 自動出現在表格
- **AND** 已展開的其他專利詳情 SHALL 保留

#### Scenario: 使用者停留無關頁面

- **WHEN** 專利備註工作完成但使用者在匯出頁
- **THEN** 前端 SHALL 不立即重抓專利列表

### Requirement: WSP-010 全庫 Workspace 唯一呈現

系統 SHALL 在 workspace API 投影每筆資料的 `is_global` 布林身分，並以該身分辨識全庫，不得依賴固定 workspace ID 或顯示名稱。

#### Scenario: 前端載入 Workspace 下拉

- **GIVEN** API 同時回傳一筆 `is_global=true` 與多筆一般 workspace
- **WHEN** 前端建立 workspace 選單
- **THEN** 全庫 SHALL 只以固定全庫入口呈現一次
- **AND** 一般清單只列出 `is_global=false` 的 workspace
- **AND** 此呈現去重不得修改任何 workspace 資料

### Requirement: WSP-011 專利列表與詳情共用正式投影

系統 SHALL 以單一 `PATENT_COLUMNS` 定義專利總覽與分類區的列表欄位；列表須保留所有正式欄位，缺值留空，正規化名稱供顯示，來源原文留在詳情供追溯，連結欄可安全開啟。

#### Scenario: 同一專利出現在兩個瀏覽區

- **WHEN** 專利同時由總覽與分類區顯示
- **THEN** 兩區 SHALL 使用相同欄位定義與顯示順序
- **AND** 不得以區域專屬複本造成欄位漂移

#### Scenario: 列表包含代表圖狀態

- **WHEN** API 回傳專利列表
- **THEN** 回應 SHALL 以 `has_figure` 表示代表圖可用性
- **AND** 不得在列表載入圖像 bytea
- **AND** 圖像內容 SHALL 由專用端點延遲讀取

#### Scenario: 批次載入列表

- **WHEN** 一頁包含多筆專利與其顯示欄位
- **THEN** API SHALL 以批次投影取得資料
- **AND** 不得為每筆專利另發 N+1 查詢

### Requirement: WSP-012 集中式 TW 待登錄狀態管理

系統 SHALL 在專利瀏覽介面提供預設收合的「TW 專利狀態管理」區塊，只列出 `country_code='TW'` 且目前 `legal_status` 為 NULL、空字串或只含空白的專利。所有經 Nginx 進入工具的內網使用者 SHALL 可逐筆完成首次登錄，系統 MUST NOT 在本功能提供批次修改、查看全部 TW 專利或修改已登錄狀態。

#### Scenario: 預設收合且只列待登錄 TW 專利
- **WHEN** 使用者開啟專利瀏覽介面
- **THEN** TW 專利狀態管理區塊 SHALL 預設收合
- **WHEN** 使用者展開區塊
- **THEN** 清單 SHALL 只包含 TW 且狀態空白的專利
- **AND** 每列 SHALL 顯示專利識別資訊、目前狀態、狀態選單與單筆儲存操作

#### Scenario: 選單值來自唯一後端契約
- **WHEN** 前端載入待登錄清單
- **THEN** 系統 SHALL 提供 `已申請`、`已公開`、`審查中`、`已核准`、`放棄`、`核駁`、`撤回`、`已失效`、`屆滿失效` 九項合法值
- **AND** 前端 SHALL 消費後端提供的值域，不得另行維護第二份狀態清單

#### Scenario: 合法首次登錄成功
- **GIVEN** 目標專利為 TW 且目前狀態空白
- **WHEN** 使用者選擇合法狀態並儲存
- **THEN** 系統 SHALL 原子地保存目前值與一筆歷程
- **AND** 回應 SHALL 表明資料已儲存及背景刷新狀態
- **AND** 前端 SHALL 從待處理清單移除該列並停留在原畫面

#### Scenario: 非法值或非 TW 被拒絕
- **WHEN** 請求狀態不在九項值域內
- **THEN** API SHALL 拒絕請求且不得修改資料
- **WHEN** 目標專利不是 TW
- **THEN** API SHALL 拒絕請求且不得新增歷程

#### Scenario: 已登錄或併發第二次提交衝突
- **GIVEN** 目標專利已具有非空 `legal_status`
- **WHEN** 使用者再次提交首次登錄
- **THEN** API SHALL 回傳 conflict
- **AND** 不得改寫目前值或 append 第二筆歷程

#### Scenario: 儲存失敗保留待處理列
- **WHEN** 狀態 API 儲存失敗
- **THEN** 前端 SHALL 保留該列與使用者選擇
- **AND** SHALL 顯示可讀錯誤，不得誤報成功

