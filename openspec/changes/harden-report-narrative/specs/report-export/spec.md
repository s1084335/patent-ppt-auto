## ADDED Requirements

### Requirement: EXP-008 解讀必須留下取證足跡

`ai:narrative` 產出的 `narratives.json` SHALL 在頂層提供 `evidence` 物件，記錄每張報表用來
支撐深入描述的取證來源。缺少、為空或本次查詢數為零時，系統 SHALL 產生可辨識的契約警告，
但 SHALL NOT 使工作失敗。

⚠ `evidence` 對組版端維持 additive：組版端 SHALL 忽略此鍵，既有 `narratives.json`
SHALL NOT 因缺少此鍵而無法顯示。

#### Scenario: 有取證且足跡完整

- **GIVEN** 一次解讀在寫作前查過資料庫
- **WHEN** 解讀完成並寫出 `narratives.json`
- **THEN** 頂層 SHALL 有 `evidence`，其鍵為 report key
- **AND** 每筆項目 SHALL 含 `claim`、`queried` 與 `patent_ids`
- **AND** SHALL NOT 產生未取證警告

#### Scenario: 完全未取證

- **GIVEN** 一次解讀從頭到尾沒有呼叫任何取證工具
- **WHEN** 解讀完成
- **THEN** 契約警告 SHALL 指出本次未取證，並標示查詢次數為零
- **AND** 工作狀態 SHALL 仍為 `succeeded`
- **AND** 已產出的解讀內容 SHALL 保留可用

#### Scenario: evidence 存在但為空

- **GIVEN** `narratives.json` 頂層有 `evidence` 但其值為空物件
- **WHEN** 契約驗證執行
- **THEN** SHALL 與缺少 `evidence` 同等處理，產生契約警告

#### Scenario: 組版端不受影響

- **GIVEN** 一份不含 `evidence` 的既有 `narratives.json`
- **WHEN** 報表重新組版
- **THEN** 組版 SHALL 正常完成
- **AND** SHALL NOT 因缺鍵而報錯或遺漏頁面

### Requirement: EXP-009 取證稽核必須隨工作結果落庫

`ai:narrative` 的工作結果 SHALL 包含 `query_audit` 與 `query_count`，並隨結果寫入
`app_layer.workflow_outputs`，使「這次查了幾次、查了什麼」不依賴執行機器上的任何檔案。

⚠ 稽核 SHALL 只記查詢行為（工具、範圍、回傳列數、是否截斷、是否失敗），
SHALL NOT 記錄查詢回傳的專利內容——稽核不得變成資料副本。

#### Scenario: 有查詢

- **GIVEN** 一次解讀呼叫了取證工具
- **WHEN** 工作完成並回存結果
- **THEN** `job_result` SHALL 含 `query_audit` 陣列與對應的 `query_count`
- **AND** 兩者 SHALL 可由 `workflow_outputs` 讀回

#### Scenario: 零查詢不得省略欄位

- **GIVEN** 一次解讀完全沒有呼叫取證工具
- **WHEN** 工作完成並回存結果
- **THEN** `query_count` SHALL 為 `0`
- **AND** `query_audit` SHALL 為空陣列
- **AND** 兩個欄位 SHALL 存在，SHALL NOT 因為值為空而被省略

#### Scenario: 稽核讀取失敗不得拖垮工作

- **GIVEN** 稽核紀錄因故讀不回來
- **WHEN** 工作完成
- **THEN** 工作 SHALL 仍依解讀本身的結果判定成敗
- **AND** `query_count` SHALL 為 `0`，使稽核缺失本身現形

### Requirement: EXP-010 解讀契約警告必須對使用者可見

解讀產生的契約警告 SHALL 隨工作結果落庫，並 SHALL 在前端 AI 任務介面顯示；
違規 SHALL NOT 只存在於執行期記憶體或伺服器日誌。

#### Scenario: 有警告

- **GIVEN** 一次解讀產生了契約警告（未取證、漏產變體或三件套超限）
- **WHEN** 使用者在前端檢視該 AI 任務
- **THEN** `job_result` SHALL 含 `contract_warnings`
- **AND** 前端 SHALL 顯示這些警告文字
- **AND** 工作狀態 SHALL 仍顯示為成功，兩者並存不互相取代

#### Scenario: 無警告

- **GIVEN** 一次解讀通過全部契約檢查
- **WHEN** 使用者在前端檢視該 AI 任務
- **THEN** `contract_warnings` SHALL 為空陣列
- **AND** 前端 SHALL NOT 顯示警告區塊

### Requirement: EXP-011 品質檢查必須覆蓋交付物實際顯示的文字

解讀的品質檢查 SHALL 施加於交付物實際呈現給使用者的那份文字。當同一變體同時具有
長文與條列時，兩者 SHALL 各自受檢；系統 SHALL NOT 只檢查其中一種形式而放過另一種。

⚠ 判準本身 SHALL 沿用既有品質規則，本需求只規範**檢查對象**，不新增判準。

#### Scenario: 只有長文的變體受檢

- **GIVEN** 一個變體只產出長文、沒有條列
- **WHEN** 品質檢查執行
- **THEN** 該長文 SHALL 逐項通過既有品質規則的檢驗
- **AND** SHALL NOT 因為沒有條列而跳過整個變體

#### Scenario: 長文違規必須現形

- **GIVEN** 一個變體的長文不含任何具體數值
- **WHEN** 品質檢查執行
- **THEN** SHALL 產生指出該變體與該項規則的警告

#### Scenario: 兩種形式並存時各自受檢

- **GIVEN** 一個變體同時有長文與條列
- **WHEN** 品質檢查執行
- **THEN** 兩者 SHALL 各自受檢
- **AND** 任一方違規 SHALL 產生可辨識到形式與變體的警告

### Requirement: EXP-012 解讀 SHALL 在交件前自檢並修稿

`ai:narrative` SHALL 在把解讀寫入工作區之前執行品質檢查。檢查未通過時，系統 SHALL 將
**具體違規項目**回饋給 CLI 要求修正，並重新檢查。修稿 SHALL 有明確輪數上限；
達上限仍未通過時，系統 SHALL 停止重試、保留最後一版產出，並將剩餘違規記入契約警告。

⚠ 自檢迴圈 SHALL 在工作內部進行，SHALL NOT 改變 `ai:narrative` 對外的成敗判定；
SHALL NOT 為了自檢而擴大 CLI 的工具權限。

#### Scenario: 首輪違規並修正後通過

- **GIVEN** CLI 首次交出的解讀有品質違規
- **WHEN** 自檢執行
- **THEN** 系統 SHALL 把違規的變體與規則具體回饋給 CLI
- **AND** CLI 修正後 SHALL 重新檢查
- **AND** 通過後才 SHALL 寫入最終產出

#### Scenario: 達輪數上限仍未通過

- **GIVEN** 修稿已達設定的輪數上限而檢查仍未通過
- **WHEN** 工作結束
- **THEN** 系統 SHALL 停止重試，SHALL NOT 無限迴圈
- **AND** SHALL 保留最後一版產出供使用者檢視
- **AND** 剩餘違規 SHALL 出現在 `contract_warnings`
- **AND** 工作狀態 SHALL 仍為 `succeeded`

#### Scenario: 首輪即通過時不觸發修稿

- **GIVEN** CLI 首次交出的解讀已通過全部檢查
- **WHEN** 自檢執行
- **THEN** SHALL NOT 發出修稿要求
- **AND** SHALL 直接寫入產出

#### Scenario: 自檢不擴權

- **WHEN** 自檢迴圈執行
- **THEN** CLI 可用的工具集 SHALL 與未啟用自檢時相同

### Requirement: EXP-013 品質判準 SHALL 依文字性質，SHALL NOT 依格式

品質檢查的判準 SHALL 以文字本身的性質為依據——是否含具體數值、是否指名對象、
是否說明成因。系統 SHALL NOT 以固定小標、固定欄位名或固定句型的出現與否作為判準，
亦 SHALL NOT 規定解讀必須使用任何特定句型。

⚠ 本需求是對第一世代 PPT「模板化」事故的直接防範：當時的固定句型是**規則自己規定的**，
產出千篇一律的原因不在模型，在判準。

#### Scenario: 判準不受格式影響

- **GIVEN** 兩段實質內容相同、但小標與句型不同的解讀文字
- **WHEN** 品質檢查執行
- **THEN** 兩者的檢查結果 SHALL 相同

#### Scenario: 空洞但格式正確的文字仍不合格

- **GIVEN** 一段依循常見句型、但不含任何具體數值也未指名對象的文字
- **WHEN** 品質檢查執行
- **THEN** SHALL 判為不合格
- **AND** 警告 SHALL 指出缺少的是實質內容，而非格式

#### Scenario: 不得引入必填句型

- **WHEN** 檢視品質檢查的全部判準
- **THEN** SHALL NOT 有任何一條以「必須出現某字串或某句型」為通過條件

### Requirement: EXP-014 限制與涵蓋範圍說明 SHALL 由程式產生

報表中說明資料涵蓋範圍、排除原因與統計口徑的文字 SHALL 由確定性程式依實際資料產生，
SHALL NOT 交由 AI 每次重寫。

⚠ 這類文字的價值在於**每次都一樣**：使用者靠它判斷數字可信度，措辭浮動會被誤讀為
口徑改變。

#### Scenario: 相同資料產生相同說明

- **GIVEN** 同一份報表資料
- **WHEN** 重複產生報表
- **THEN** 限制與涵蓋範圍說明的文字 SHALL 完全相同

#### Scenario: AI 不覆寫限制說明

- **GIVEN** 一次解讀已完成
- **WHEN** 解讀結果寫回工作區
- **THEN** SHALL NOT 改動由程式產生的限制與涵蓋範圍說明

### Requirement: EXP-025 給 CLI 的內部指示 SHALL NOT 洩漏進解讀文字

派工提示詞中給 CLI 的作業指示——寫作限制、字數上限、輸出格式要求、禁止事項、
工具使用規則、契約欄位名——SHALL NOT 出現在解讀文字中。解讀 SHALL 只呈現對報表
資料的判讀。

⚠ 洩漏的常見形式不是整段複製，而是**用自己的話覆述**（「依規定本段不超過 N 字」
「本次未取得足夠資料故不作推論」）。檢查對象是**內容性質**，不是字串比對。

#### Scenario: 不覆述寫作限制

- **WHEN** 檢視產出的解讀文字
- **THEN** SHALL NOT 出現對字數、段數、格式或句型要求的描述

#### Scenario: 不揭露契約與工具細節

- **WHEN** 檢視產出的解讀文字
- **THEN** SHALL NOT 出現契約欄位名、工具名稱或取證機制的描述

#### Scenario: 不以內部指示替代判讀

- **GIVEN** 某張報表的資料不足以支撐深入判讀
- **WHEN** 解讀產出
- **THEN** SHALL 據實說明資料本身的狀況
- **AND** SHALL NOT 以「依規定」「依指示」之類的內部依據作為說法

### Requirement: EXP-015 解讀 SHALL 可人工編輯並保存

使用者 SHALL 可在報表介面直接編輯任一報表變體的解讀文字並保存。保存後重新載入
SHALL 仍為人工稿。人工稿與 AI 原稿 SHALL 分欄保存，顯示時 SHALL 以人工稿優先，
並 SHALL 標示該段已經人工修改。

⚠ 匯出的 HTML 檔為靜態交付物：SHALL 包含人工稿的**內容**，SHALL NOT 包含編輯功能。

#### Scenario: 編輯並保存

- **GIVEN** 使用者檢視某報表變體的解讀
- **WHEN** 使用者修改文字並保存
- **THEN** 系統 SHALL 保存該人工稿
- **AND** 重新載入後 SHALL 顯示人工稿

#### Scenario: 分欄保存且人工稿優先

- **GIVEN** 某變體同時有 AI 原稿與人工稿
- **WHEN** 介面顯示該變體
- **THEN** SHALL 顯示人工稿
- **AND** AI 原稿 SHALL 仍被保存，SHALL NOT 被覆蓋
- **AND** 介面 SHALL 標示該段已人工修改

#### Scenario: 未編輯的變體顯示 AI 原稿

- **GIVEN** 某變體沒有人工稿
- **WHEN** 介面顯示該變體
- **THEN** SHALL 顯示 AI 原稿
- **AND** SHALL NOT 標示為已人工修改

#### Scenario: 匯出檔含內容不含功能

- **GIVEN** 某些變體已有人工稿
- **WHEN** 使用者匯出 HTML 報表
- **THEN** 匯出檔 SHALL 呈現人工稿的內容
- **AND** 匯出檔 SHALL NOT 提供編輯或保存操作

### Requirement: EXP-016 解讀的產生與編輯 SHALL 逐報表獨立

每張報表的解讀 SHALL 是獨立單位。重新產生某張報表的解讀 SHALL NOT 影響其他報表
既有的 AI 原稿或人工稿；編輯某張報表的解讀亦 SHALL NOT 影響其他報表。

#### Scenario: 重跑單張不影響其他張

- **GIVEN** 報表 A 與報表 B 皆有解讀，且 B 有人工稿
- **WHEN** 使用者只重新產生報表 A 的解讀
- **THEN** 報表 A 的 AI 原稿 SHALL 更新
- **AND** 報表 B 的 AI 原稿與人工稿 SHALL 保持不變

#### Scenario: 編輯單張不影響其他張

- **GIVEN** 多張報表皆有解讀
- **WHEN** 使用者編輯其中一張並保存
- **THEN** 其他報表的解讀 SHALL 保持不變

#### Scenario: 重跑指定報表時該張以 AI 新稿為準

- **GIVEN** 報表 A 已有人工稿
- **WHEN** 使用者明確對報表 A 重新產生解讀
- **THEN** 報表 A 的 AI 原稿 SHALL 更新為新稿
- **AND** 系統 SHALL 讓使用者能辨識該張的人工稿與新 AI 稿之間的關係
