## MODIFIED Requirements

### Requirement: WSP-012 集中式 TW 待登錄狀態管理

系統 SHALL 提供獨立的「TW 專利狀態管理」頁面，只列出 `country_code='TW'` 且目前
`legal_status` 為 NULL、空字串或只含空白的專利。專利瀏覽介面 SHALL 提供進入該頁的入口，
且入口 SHALL 顯示待登錄件數。所有經 Nginx 進入工具的內網使用者 SHALL 可逐筆完成首次登錄，
系統 MUST NOT 在本功能提供批次修改、查看全部 TW 專利或修改已登錄狀態。

#### Scenario: 預設收合且只列待登錄 TW 專利
- **WHEN** 使用者開啟專利瀏覽介面
- **THEN** 介面 SHALL 顯示 TW 專利狀態管理的入口與待登錄件數
- **AND** 待登錄清單 SHALL NOT 直接展開於瀏覽介面
- **WHEN** 使用者由該入口進入獨立頁
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

## ADDED Requirements

### Requirement: WSP-014 瀏覽首頁只承載入口

專利瀏覽介面 SHALL 只承載專利表本身、搜尋，以及各項作業的入口。
需要編輯名單的作業 SHALL 位於各自的獨立頁面。

每個入口 SHALL 顯示該作業的待辦數量，且該數量 SHALL 取自後端權威端點，
SHALL NOT 由前端自行計算。

作業頁面 SHALL NOT 出現在左側導覽，SHALL 僅能自瀏覽介面的入口進入。

#### Scenario: 入口顯示待辦數
- **WHEN** 使用者開啟專利瀏覽介面
- **THEN** 每個作業入口 SHALL 顯示其待辦數量

#### Scenario: 待辦數與後端一致
- **WHEN** 後端待辦數量變動後重新載入瀏覽介面
- **THEN** 入口顯示的數量 SHALL 與後端端點回傳值相同

#### Scenario: 無待辦時入口仍可用
- **WHEN** 某作業目前待辦為 0
- **THEN** 入口 SHALL 仍顯示且可進入

#### Scenario: 作業頁不進左導覽
- **WHEN** 使用者檢視左側導覽
- **THEN** 作業頁 SHALL NOT 出現為導覽項目
