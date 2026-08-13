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
