# Company Governance Specification

## Purpose

定義申請人／專利權人代碼歸戶、名稱收斂、中文名確認與顯示優先序，讓 importer、derived、API 與報表共用一致公司身分，同時保留來源原文、AI 草稿與人工正式值之間的追溯邊界。
## Requirements
### Requirement: CMP-001 有代碼依代碼歸戶

系統 SHALL 將具有 WIPS 公司代碼的名稱依代碼歸戶；未建組代碼可自動建立待確認群組，但不得自動填入臆測中文名。

#### Scenario: 新代碼首次出現

- **WHEN** 匯入資料含尚未建組的公司代碼
- **THEN** 系統 SHALL 建立 `review_required` 群組
- **AND** 中文名保持空白

### Requirement: CMP-002 無代碼不做模糊自動寫入

系統 SHALL 只對完全命中的無代碼名稱自動歸戶；疑似相近名稱只能提示人工處理，不得以相似度直接改正式資料。

#### Scenario: 名稱只有部分相似

- **WHEN** 新名稱與既有公司名稱僅部分相似
- **THEN** 系統 SHALL 不自動加入既有群組

### Requirement: CMP-003 AI 中文名先草稿後確認

系統 SHALL 將 AI 產生的公司中文名保存為草稿，只有使用者 confirm 或 edit 後才可成為正式顯示名稱；reject 不得污染正式顯示。

#### Scenario: AI 草稿尚未確認

- **WHEN** 中文名草稿已產生但尚未確認
- **THEN** 專利列表與報表 SHALL 不使用該草稿作正式顯示名

### Requirement: CMP-004 顯示名稱優先序

系統 SHALL 依「已確認中文名、正規化收斂名、標準化名、來源原文」的順序產生顯示名稱，並保留原文欄位。

#### Scenario: 已確認中文名存在

- **WHEN** 公司群組有 confirmed 中文名
- **THEN** derived、列表與報表 SHALL 優先顯示中文名
- **AND** 原始申請人／專利權人字面仍可查詢

### Requirement: CMP-005 維護操作可追溯

系統 SHALL 提供待確認、既有群組、未歸戶名稱、變體、promote、edit 與 delete 等維護操作，並在改動後刷新受影響 projection。

#### Scenario: 確認或編輯群組

- **WHEN** 使用者完成公司治理寫入
- **THEN** 系統 SHALL 觸發或要求 derived refresh
- **AND** 後續顯示使用更新後的唯一收斂結果

### Requirement: CMP-006 待審集團建議證據可讀呈現

The system SHALL present persisted CLI/AI company-group suggestion evidence in a readable review format without changing its review-only status or confirmation workflow.

#### Scenario: User reviews readable CLI AI evidence in the browser

- **GIVEN** CLI/AI has persisted a review-only group suggestion
- **WHEN** an internal browser user opens group normalization
- **THEN** each suggested member SHALL show its company name, optional company code, and localized confidence
- **AND** each evidence source SHALL show a compact link with the fixed text `來源` and its supporting claim
- **AND** warnings SHALL be shown separately from evidence sources
- **AND** raw evidence JSON SHALL NOT be displayed
- **AND** AI-provided text SHALL be HTML escaped and only HTTPS evidence URLs SHALL be linked
- **AND** missing optional evidence fields SHALL show a readable fallback
- **AND** the existing confirm and reject actions SHALL remain available

### Requirement: CMP-007 集團正規化可完整復原

The system SHALL let an internal browser user reverse confirmed company-group mappings without changing normalized company records or patent records.

#### Scenario: User undoes a confirmed AI suggestion

- **GIVEN** an AI-suggested member was confirmed by a user and still has its evidence sources
- **WHEN** the user selects `撤銷確認`
- **THEN** the member SHALL return to `suggested`
- **AND** its evidence SHALL remain unchanged
- **AND** the parent group review status SHALL be recomputed from its remaining members
- **AND** the member SHALL reappear in pending review

#### Scenario: User removes one group member

- **GIVEN** a confirmed group contains one or more members
- **WHEN** the user removes one member
- **THEN** only that member mapping SHALL be deleted
- **AND** normalized company records and patent records SHALL remain unchanged

#### Scenario: User dissolves a group

- **GIVEN** a group mapping exists
- **WHEN** the user confirms `解散集團`
- **THEN** the group and all of its member mappings SHALL be deleted
- **AND** normalized company records and patent records SHALL remain unchanged

#### Scenario: Reversal refreshes open browsers

- **WHEN** an AI confirmation is undone, one member is removed, or a group is dissolved
- **THEN** the committed transaction SHALL publish a `companyGroups` SSE refresh event

### Requirement: CMP-009 AI 可建議加入受控既有集團

The system SHALL let the manually triggered company-group research job suggest that an ungrouped
confirmed company belongs to an existing confirmed group without directly activating the mapping.

#### Scenario: CLI suggests an existing confirmed group

- **GIVEN** the backend supplies an ungrouped company and a controlled list of confirmed groups
- **WHEN** the CLI returns a whitelisted `target_group_id` with HTTPS evidence
- **THEN** the system SHALL add only a `suggested` member mapping under that existing group
- **AND** it SHALL NOT create or rename a parent group
- **AND** reports SHALL remain unchanged until a browser user confirms the member

#### Scenario: CLI invents or targets an unavailable group

- **WHEN** the CLI returns a group ID absent from the backend-controlled confirmed-group list
- **THEN** the system SHALL reject the result before persistence

#### Scenario: User reviews an existing-group target

- **WHEN** an existing-group membership suggestion is shown in the browser
- **THEN** the existing group name SHALL be displayed as a read-only target
- **AND** confirming SHALL change only the selected member mapping
- **AND** rejecting SHALL preserve the existing group and its confirmed members

#### Scenario: CLI proposes a new group

- **WHEN** evidence supports a group not present in the controlled confirmed-group list
- **THEN** the existing new-group suggestion workflow SHALL remain available
- **AND** its pending group name SHALL remain editable before confirmation

### Requirement: CMP-008 待審集團名稱可於確認前編輯

The system SHALL let an internal browser user edit a CLI/AI-suggested group name before confirming
an individual member mapping.

#### Scenario: User confirms a suggestion with a shortened group name

- **GIVEN** a pending CLI/AI group suggestion has an overly long group name
- **WHEN** the user edits the pending name and confirms one suggested member
- **THEN** the trimmed edited name and that member confirmation SHALL be persisted atomically
- **AND** other suggested members SHALL retain their existing review status
- **AND** the committed transaction SHALL publish the existing `companyGroups` SSE refresh event

#### Scenario: Edited name is invalid

- **WHEN** the submitted group name is blank or exceeds 255 characters
- **THEN** the system SHALL reject the request without confirming the member or renaming the group

#### Scenario: Existing decision clients remain compatible

- **WHEN** a client confirms without a request body or rejects a suggestion
- **THEN** the existing group name SHALL remain unchanged
- **AND** the selected member decision SHALL follow the existing workflow

### Requirement: CMP-010 一個別稱只屬於一個公司代碼

系統 SHALL 確保任一已確認的公司別稱只對應一個公司代碼，使歸戶結果不依賴查詢順序。

⚠ 本要求 SHALL NOT 被解讀為「一家公司只能有一個代碼」——一家公司在 WIPS
擁有多個代碼是常態，由集團層收攏。

#### Scenario: 同一別稱不得對到多個代碼

- **GIVEN** 某個別稱查找鍵已被一個公司代碼以 `confirmed` 狀態使用
- **WHEN** 寫入或確認另一筆使用同一查找鍵、但屬於不同代碼的別稱
- **THEN** 系統 SHALL 拒絕該寫入
- **AND** 拒絕 SHALL 由資料庫約束保證，不倚賴呼叫端自行檢查

#### Scenario: 多代碼歸屬同一集團仍合法

- **GIVEN** 來源資料（WIPS）為同一家公司登記了多個公司代碼
- **WHEN** 這些代碼各自帶著自己的別稱被寫入系統
- **THEN** 系統 SHALL 允許這些代碼並存
- **AND** SHALL 由集團成員關係表達它們屬於同一集團
- **AND** SHALL NOT 因為代碼數量大於一而視為錯誤

#### Scenario: 待審建議暫時允許重複

- **GIVEN** 某別稱已被一個代碼以 `confirmed` 狀態使用
- **WHEN** AI 建議或待審草稿產生同一別稱
- **THEN** 系統 SHALL 允許該草稿存在
- **AND** 使用者確認時 SHALL 因違反唯一性而被拒絕
- **AND** 拒絕訊息 SHALL 說明是哪個別稱與哪個代碼衝突，SHALL NOT 只回資料庫層錯誤

### Requirement: CMP-011 別稱歸屬以來源權威資料為準

系統的別稱歸屬 SHALL 以專利資料來源（WIPS）登記的標準申請人別稱清單為準；
人工登錄與來源不符時 SHALL 以來源為準修正。

#### Scenario: 別稱不在來源清單中

- **GIVEN** 來源（WIPS）登記了各公司代碼的標準申請人別稱清單
- **WHEN** 某公司代碼下的別稱未出現在來源的該代碼別稱清單
- **AND** 該別稱出現在另一個代碼的來源清單中
- **THEN** 系統 SHALL 將該別稱歸還至來源指定的代碼
- **AND** 被歸還的代碼 SHALL 保留其公司層資料（不因失去一個別稱而消失）

### Requirement: CMP-012 集團成員涵蓋同集團的全部公司代碼

集團成員關係 SHALL 涵蓋屬於該集團的所有公司代碼；遺漏成員會使集團層統計低估，
且不會產生任何錯誤訊息。

#### Scenario: 離岸或子公司代碼須納入集團

- **GIVEN** 系統中已定義該集團
- **WHEN** 某公司代碼在來源資料中的名稱顯示它屬於該集團
- **THEN** 該代碼 SHALL 被登記為該集團成員
- **AND** 集團層統計 SHALL 涵蓋其專利件數

### Requirement: CMP-013 公司代碼的刪除與轉正

刪除或轉正公司代碼時，系統 SHALL 保證所有參照該代碼的資料一致；
呼叫端 SHALL NOT 需要自行記得更新哪些關聯資料表。

#### Scenario: 刪除被集團引用的代碼

- **GIVEN** 一個公司代碼仍登記為某集團的成員
- **WHEN** 使用者刪除該公司代碼
- **THEN** 系統 SHALL 回應衝突狀態並說明原因
- **AND** 訊息 SHALL 指出該代碼卡在集團與下一步操作
- **AND** SHALL NOT 回應無法理解的資料庫層錯誤
- **AND** 判斷 SHALL 由資料庫約束作出，SHALL NOT 改以「先查詢再刪除」替代

#### Scenario: 刪除未被集團引用的代碼

- **GIVEN** 一個公司代碼不屬於任何集團
- **WHEN** 使用者刪除該公司代碼
- **THEN** 刪除 SHALL 成功並照常排入 derived refresh
- **AND** 系統 SHALL NOT 因新增的約束而阻擋此類刪除

#### Scenario: 臨時代碼轉正

- **GIVEN** 一個臨時代碼已登記且為某集團的成員
- **WHEN** 使用者將它換成來源查得的正式代碼
- **THEN** 該代碼的別稱與集團成員關係 SHALL 一併變更
- **AND** 集團成員的變更 SHALL 由資料庫連動保證，不倚賴呼叫端逐表更新
- **AND** 集團統計 SHALL NOT 因轉正而遺漏該公司

#### Scenario: 轉正目標已存在

- **GIVEN** 使用者填入的目標代碼已經存在於系統
- **WHEN** 執行轉正
- **THEN** 系統 SHALL 拒絕並說明這是合併而非轉正
- **AND** SHALL 指引使用者改走合併流程

### Requirement: CMP-014 新增公司代碼的單一登記處

任何路徑建立集團成員關係時 SHALL 先於公司實體表登記該代碼；
集團成員 SHALL NOT 指向未登記的代碼。

⚠ 別稱表不受此約束保護（2026-08-18 範圍裁決）。正常寫入路徑會一併登記，
但直接以 SQL 寫入別稱不會被擋。

#### Scenario: AI 建議確認產生新代碼

- **GIVEN** 一筆 AI 建議需要建立系統中尚未存在的公司代碼
- **WHEN** 使用者確認該建議
- **THEN** 系統 SHALL 先登記該代碼於公司實體表
- **AND** 再寫入其別稱與集團成員關係

#### Scenario: 未登記的代碼不得建立集團成員

- **GIVEN** 某個代碼未登記於公司實體表
- **WHEN** 任何路徑試圖以該代碼寫入集團成員
- **THEN** 系統 SHALL 拒絕該寫入

### Requirement: CMP-015 查證過的候選必須留下紀錄

系統 SHALL 記錄每個正規化候選被送出查證的事實，包含當時的專利件數；
查證結果 SHALL NOT 只存在於該次執行的回傳值中。

#### Scenario: 查證後留下可查的紀錄

- **GIVEN** 一個正規化候選被送進 AI 查證
- **WHEN** 該段查證完成
- **THEN** 系統 SHALL 記錄它被問過、當時的專利件數與結果類別
- **AND** 紀錄 SHALL 區分「查無證據」與「已產生建議」

#### Scenario: 查證失敗的段不留下已問紀錄

- **GIVEN** 某一段查證因回傳格式或協定違反而被拒絕
- **WHEN** 該段結束
- **THEN** 該段的候選 SHALL NOT 被記為已問過
- **AND** 它們 SHALL 在下次執行時重新被取到
- **AND** 系統 SHALL NOT 把協定錯誤當成「這些候選查無證據」

### Requirement: CMP-016 候選依排隊順序查證

系統 SHALL 讓未曾查證過的候選排在已查證過的候選之前；
任一候選 SHALL NOT 在所有候選都被查證過一輪之前被查證第二次。

#### Scenario: 沒問過的排在問過的前面

- **GIVEN** 候選 A 從未被查證、候選 B 已被查證過
- **WHEN** 系統取下一批候選
- **THEN** A SHALL 排在 B 之前

#### Scenario: 同層依專利件數決定先後

- **GIVEN** 兩個都未曾查證的候選
- **WHEN** 系統決定先後
- **THEN** SHALL 以專利件數多者優先

### Requirement: CMP-017 查無證據的候選只在有新資料時重問

已查證過的候選 SHALL 只在其專利件數增加後重新進入查證隊列；
系統 SHALL NOT 僅因為時間經過就重複查證同一個候選。

#### Scenario: 件數沒變不重問

- **GIVEN** 某候選已被查證過且查無證據
- **AND** 該名稱的專利件數與查證當時相同
- **WHEN** 系統取下一批候選
- **THEN** 該候選 SHALL NOT 被取到

#### Scenario: 有新專利就重新入列

- **GIVEN** 某候選已被查證過
- **WHEN** 該名稱的專利件數增加
- **THEN** 該候選 SHALL 重新進入查證隊列

### Requirement: CMP-018 查證分批且逐段隔離失敗

系統 SHALL 限制單次執行的候選數量，並將其切分為多段各自呼叫；
任一段失敗 SHALL NOT 影響其他段的結果。

#### Scenario: 單次執行有上限並分段

- **GIVEN** 候選數量超過單次上限
- **WHEN** 執行一次查證
- **THEN** 系統 SHALL 只取上限數量的候選
- **AND** SHALL 將它們切分為固定大小的多段，逐段呼叫

#### Scenario: 一段協定錯誤不影響其他段

- **GIVEN** 某一段的回傳違反輸出契約
- **WHEN** 該段被拒絕
- **THEN** 其餘各段的建議 SHALL 照常寫入
- **AND** 系統 SHALL NOT 因單段失敗而讓整次執行零產出

#### Scenario: 段內缺證據仍只跳過該筆

- **GIVEN** 某段中有一筆建議缺少證據
- **WHEN** 驗證該段
- **THEN** SHALL 只跳過該筆
- **AND** 同段其他建議 SHALL 照常寫入

### Requirement: CMP-019 未完成的工作量必須顯示

系統 SHALL 讓使用者看到尚未查證的候選數量，以及失敗的段數與原因；
「一批執行完成」SHALL NOT 被呈現為「所有候選都已查證」。

#### Scenario: 顯示剩餘候選數

- **GIVEN** 一次查證只處理了部分候選
- **WHEN** 使用者查看正規化建議區
- **THEN** 系統 SHALL 顯示尚有多少候選未查證
- **AND** SHALL 區分「從未查證」與「因有新專利而待重查」

#### Scenario: 顯示失敗的段

- **GIVEN** 某次執行中有段被拒絕
- **WHEN** 使用者查看結果
- **THEN** 系統 SHALL 顯示失敗的段數與原因
- **AND** SHALL NOT 只呈現成功的部分

### Requirement: CMP-020 待審建議不得被其他流程靜默清除

系統 SHALL 只清除自己流程產生的 AI 草稿；其他 AI 線的待審建議 SHALL NOT
因為共用同一個待審狀態而被一併刪除。

#### Scenario: 確認中文名草稿

- **GIVEN** 某公司代碼同時有中文名草稿與正規化待審建議
- **WHEN** 使用者確認該代碼的中文名草稿
- **THEN** 只有中文名草稿 SHALL 被清除
- **AND** 正規化待審建議 SHALL 保留

### Requirement: CMP-021 內部識別鍵不得進入 AI 提示

送給 CLI 的候選資料 SHALL 只包含供其引用的公開欄位；
內部識別鍵 SHALL NOT 出現在提示內容中。

#### Scenario: 提示只帶公開欄位

- **GIVEN** 候選資料同時含有內部識別鍵與公開欄位
- **WHEN** 組出給 CLI 的提示
- **THEN** 提示 SHALL 只包含公開欄位
- **AND** 內部識別鍵 SHALL NOT 出現在提示字串中
