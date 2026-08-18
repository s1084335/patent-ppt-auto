## MODIFIED Requirements

### Requirement: CMP-003 AI 公司正規化先建議後確認

系統 SHALL 將 AI 對公司原始變體、中英文正規化名稱與目標公司身分的判斷保存為 `ai_suggested` 待審資料；只有使用者確認或修改後確認的變體才可成為正式 `confirmed` mapping。AI 建議、略過與失敗輸出不得成為專利列表、分析、分群或報表的正式公司名稱。

#### Scenario: AI 建議尚未確認

- **GIVEN** AI 已對一個或多個原始變體產生中英文正規化與歸戶建議
- **WHEN** 使用者尚未確認
- **THEN** 系統 SHALL 保存原始變體與待審建議
- **AND** confirmed-only projection、專利列表、分析、分群與報表 SHALL 保持不變

#### Scenario: 使用者略過建議

- **WHEN** 使用者選擇略過
- **THEN** 系統 SHALL 不建立或修改正式 mapping
- **AND** 待審建議 SHALL 保留供稍後處理

#### Scenario: AI job 或輸出驗證失敗

- **WHEN** CLI 失敗、回傳非法 JSON、未知候選、未知目標或不合契約欄位
- **THEN** 系統 SHALL 將 job 標為失敗或拒絕該筆輸出
- **AND** 系統 SHALL NOT 留下部分 confirmed mapping

## ADDED Requirements

### Requirement: CMP-010 AI 不得產生 WIPS 公司代碼

系統 SHALL 將 WIPS 公司代碼視為 Backend 控制的權威資料。AI 輸出不得提供可自由填寫的 WIPS code 欄位，AI 不得產生、猜測、修改或替換 WIPS 公司代碼。

#### Scenario: AI 建議加入既有公司

- **GIVEN** Backend 提供 target reference 與既有 WIPS code 的 confirmed 公司白名單
- **WHEN** AI 回傳白名單內的 target reference
- **THEN** Backend SHALL 從權威白名單解析公司與代碼
- **AND** AI SHALL NOT 回傳或覆寫 WIPS code

#### Scenario: AI 回傳未知目標或額外代碼

- **WHEN** AI 回傳白名單外 target、額外 WIPS code 或 code override
- **THEN** Backend SHALL 在 persistence 前拒絕該筆輸出
- **AND** 不得以模型文字或名稱相似推測代碼

#### Scenario: 建立沒有 WIPS 代碼的新公司

- **GIVEN** 未歸戶變體沒有權威 WIPS code
- **WHEN** 使用者確認建立新公司
- **THEN** Backend SHALL 以既有確定性規則產生 `TEMP:*`
- **AND** TEMP 代碼不得取自 AI 輸出
- **AND** 真 WIPS code 仍須由使用者查證後透過既有 promote 流程補入

### Requirement: CMP-011 每個變體可收斂為共用中英文公司身分

系統 SHALL 對每個候選原始變體保留來源字面，並可建議其目標公司、中文正式名與英文正規化名稱。同一目標公司的 confirmed 變體 SHALL 共用一組公司層中英文正式名。

#### Scenario: 多個變體加入既有公司

- **GIVEN** 多個變體被建議加入同一 confirmed 公司
- **WHEN** 使用者選取其中一筆或多筆並確認
- **THEN** 系統 SHALL 對每個選取變體建立 alias mapping
- **AND** 每筆 SHALL 使用同一 WIPS code、中文正式名與英文正規化名稱
- **AND** 未選取變體 SHALL 保持待審

#### Scenario: 使用者改選目標公司

- **GIVEN** AI 建議的目標不正確
- **WHEN** 使用者從 Backend 選單改選後確認
- **THEN** 系統 SHALL 使用使用者選定公司的權威 code 與中英文正式名
- **AND** 不得保留 AI 原目標為正式 mapping

#### Scenario: 使用者修改中英文正式名

- **WHEN** 使用者修改 AI 建議名稱後確認
- **THEN** 系統 SHALL 原子地以使用者值更新公司身分及所選變體
- **AND** derived refresh 後同公司既有 confirmed 變體 SHALL 顯示一致
- **AND** 原始 alias 字面 SHALL 保持不變

#### Scenario: 確認時發生歸戶衝突

- **GIVEN** 建議產生後某變體已被 confirmed 到不同公司
- **WHEN** 使用者確認舊建議
- **THEN** 系統 SHALL 原子拒絕並回傳可讀原因
- **AND** 不得覆蓋既有 mapping 或部分寫入

### Requirement: CMP-012 缺中文名公司可取得有依據的法人中文名稱建議

系統 SHALL 將已有權威 WIPS code 但缺中文名的公司納入候選。AI 查得市場慣用中文名或法人登記中文名稱時可提出待審建議，但 SHALL 標示名稱依據與來源，且不得自動覆蓋既有非空 confirmed 中文名。

#### Scenario: 查得市場慣用中文名

- **GIVEN** 有 WIPS code 的公司缺 confirmed 中文名
- **WHEN** 可信來源支持市場慣用中文名
- **THEN** 建議 SHALL 標示 `zh_name_basis='market_common_name'`
- **AND** 待審畫面 SHALL 顯示來源與理由

#### Scenario: 只查得法人登記中文名稱

- **GIVEN** 公司缺中文名且無足夠證據支持市場慣用名
- **WHEN** 可核對來源提供法人登記中文名稱
- **THEN** AI MAY 建議該名稱並標示 `zh_name_basis='registered_legal_name'`
- **AND** 不得把法人登記名稱呈現為市場慣用名

#### Scenario: 中文名稱缺乏來源

- **WHEN** AI 只能翻譯、音譯或依模型記憶猜測
- **THEN** 系統 SHALL 不建立可確認的中文名建議
- **AND** 公司 SHALL 保持缺中文名狀態

### Requirement: CMP-013 有確切關係證據的自然人可作為公司分析變體

系統 SHALL 允許 AI 建議將自然人原始字面歸入公司，但必須標示為 `person_affiliation`，並有證據辨識同一自然人及其與目標公司的 owner、proprietor 或董事關係。此 mapping 是人工確認的分析歸戶，不得宣稱自然人與法人在法律上為同一主體。

#### Scenario: 公司所有人或董事具有確切證據

- **GIVEN** 候選是一名自然人
- **AND** 可核對來源辨識同一人並記載其為目標公司的 owner、proprietor 或董事
- **WHEN** AI 提出 `person_affiliation`
- **THEN** 建議 SHALL 顯示人物同一性、公司關係、角色、證據日期與來源
- **AND** 確認前 SHALL 顯示「該個人名下相關專利將納入此公司統計」警示
- **AND** 只有使用者明確確認後才可建立正式 mapping

#### Scenario: 只有不足以歸戶的角色或同名

- **WHEN** 證據只顯示 founder、CEO、經理、員工、發明人、聯絡人或無法排除同名
- **THEN** 系統 SHALL 拒絕產生可確認的 `person_affiliation`
- **AND** 不得把職稱、發明人紀錄或名稱相似當成所有人／董事證據

#### Scenario: 使用者確認自然人分析歸戶

- **WHEN** 使用者確認 `person_affiliation`
- **THEN** 系統 SHALL 保留自然人原始字面、角色與證據 metadata
- **AND** confirmed projection MAY 將該人的相關專利歸入目標公司統計
- **AND** 專利來源欄與原始專利權人字面 SHALL 保持可追溯

### Requirement: CMP-014 AI 建議採手動、集中且即時刷新

系統 SHALL 在既有公司治理區提供單一、預設收合的 AI 建議入口。無待審建議時區塊 SHALL 隱藏；有建議時 SHALL 可讀呈現並允許多選確認，committed 變更 SHALL 透過 SSE 背景刷新且不得導頁。

#### Scenario: 使用者手動產生建議

- **WHEN** 使用者按下「產生 AI 建議」
- **THEN** 系統 SHALL 建立一個 `ai:company_normalization_suggestion` job
- **AND** page load、匯入與 derived refresh SHALL NOT 自動建立
- **AND** 同類 job 執行中 SHALL 防止重複建立

#### Scenario: 使用者審核可讀建議

- **GIVEN** 存在待審建議
- **WHEN** 使用者展開待審區
- **THEN** 每項 SHALL 顯示原始變體、類型、目標、中英文名、信心、理由、注意事項與來源
- **AND** 法人中文名 SHALL 顯示名稱依據，自然人 SHALL 顯示角色與分析歸戶警示
- **AND** HTTPS 證據連結文字 SHALL 固定為 `來源`
- **AND** raw JSON SHALL NOT 顯示，AI 文字 SHALL HTML escape

#### Scenario: 完成後背景刷新

- **WHEN** 建議持久化、使用者確認或 derived refresh committed
- **THEN** 系統 SHALL 發布公司治理資源 SSE invalidation
- **AND** 前端 SHALL 重查權威 API 並保留頁面、收合狀態及未提交選擇
- **AND** SSE 斷線時 SHALL 由既有輪詢與重連補償保底
