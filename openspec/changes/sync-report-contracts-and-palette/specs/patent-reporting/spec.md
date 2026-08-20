# patent-reporting（delta）

## ADDED Requirements

### Requirement: 報表彙總一律以 workspace 母體為範圍

系統 SHALL 確保所有進入報表輸出的彙總數字都以該次報表的 workspace 母體
（`patent_ids`）為範圍；繞過報表引擎自行查詢資料庫的彙總 SHALL 帶母體條件，
或列入顯式白名單並註明理由。

#### Scenario: 直查資料庫的彙總必須帶母體

- **WHEN** 程式碼在報表產製流程中自行執行 SQL 彙總
- **THEN** 該查詢 SHALL 以 `patent_ids` 或等效成員條件限定範圍
- **AND** 若不限定，SHALL 出現在全庫用途白名單並註明理由
- **AND** 未限定且不在白名單時，一致性檢查 SHALL 失敗

#### Scenario: 專利種類三分法以母體為準

- **WHEN** 系統輸出封面或母體說明的發明／新型／設計件數
- **THEN** SHALL 只計入該 workspace 母體內的專利
- **AND** SHALL NOT 回傳全庫統計

#### Scenario: 家族數以母體為準

- **WHEN** 系統輸出任何家族數
- **THEN** SHALL 以 `count(DISTINCT "WIPS同族ID")` 計算，範圍限於母體
- **AND** SHALL NOT 取自不支援母體過濾的衍生層快照
- **AND** 家族數 SHALL NOT 大於同母體的專利件數

#### Scenario: 缺同族識別碼的處理

- **WHEN** 專利缺少 `WIPS同族ID`
- **THEN** 該專利 SHALL 各自視為一個家族
- **AND** SHALL NOT 將多件缺值專利合併為單一「未知」家族

### Requirement: 封面數字由報表引擎產出

系統 SHALL 於 `report_data.json` 產出封面所需的四組數字：專利件數、家族數、
受理局數、專利類型三分法；下游簡報產製端 SHALL 只消費不自行計算。

#### Scenario: 一方產生、一方消費

- **WHEN** 簡報產製端組裝封面數字磚
- **THEN** SHALL 讀取 `report_data.json` 提供的數字
- **AND** SHALL NOT 自行查詢資料庫或由報表列反推

#### Scenario: 專利類型呈現為單一數字磚

- **WHEN** 封面呈現專利類型
- **THEN** SHALL 以一格呈現三個數字（發明·新型·設計）
- **AND** 標籤 SHALL 使用「設計」而非「外觀」

### Requirement: 中間量不進數據表

系統 SHALL NOT 在數據表顯示僅供推導用的中間量；此類資料 SHALL 保留於
`chart_rows` 供解讀取證，但不佔用表格欄位。

#### Scenario: 年度趨勢表不顯示家族數

- **WHEN** 系統輸出申請趨勢或授權公告趨勢的數據表
- **THEN** SHALL NOT 顯示家族數欄位
- **AND** SHALL 於 `chart_rows` 保留家族數供解讀端查用

## MODIFIED Requirements

### Requirement: 外觀保護策略報表

系統 SHALL 提供 `design_protection_detail` 報表與對應 section，用於輸出
**設計**保護策略與技術專利交叉 evidence。報表顯示名稱與欄位用詞 SHALL 使用
「設計」；「外觀」一詞 SHALL 僅用於描述產品造形本身，不用於指稱專利類型。

#### Scenario: 外觀判定包含 S 與 S1

- **WHEN** 報表資料列的 `document_kind` 為 `S` 或 `S1`
- **THEN** 系統 SHALL 將該列視為設計專利
- **AND** SHALL NOT 只以 `patent_type='P'` 判斷技術專利

#### Scenario: 用詞一致

- **WHEN** 系統輸出專利類型相關的欄名、圖例或標籤
- **THEN** SHALL 使用「設計」
- **AND** 描述產品造形的既有文字（如文獻備註內容）SHALL NOT 被改寫
