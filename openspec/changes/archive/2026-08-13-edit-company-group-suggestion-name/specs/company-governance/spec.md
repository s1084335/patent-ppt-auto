## ADDED Requirements

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
