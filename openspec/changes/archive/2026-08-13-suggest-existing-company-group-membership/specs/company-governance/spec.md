## ADDED Requirements

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
