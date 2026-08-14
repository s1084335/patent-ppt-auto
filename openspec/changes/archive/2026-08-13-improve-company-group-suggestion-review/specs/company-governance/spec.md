## ADDED Requirements

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
