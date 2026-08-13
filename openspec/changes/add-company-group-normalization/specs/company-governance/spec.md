## ADDED Requirements

### Requirement: Group Normalization Layer

The system SHALL provide a group normalization layer above company normalization, without overwriting WIPS company codes, company aliases, raw patent data, or company-level display names.

#### Scenario: Company and group normalization stay separate

- **GIVEN** two WIPS company codes resolve to two normalized company display names
- **WHEN** a user assigns both companies to one confirmed group
- **THEN** the company display names SHALL remain unchanged
- **AND** the group mapping SHALL be stored as a separate layer

### Requirement: Manual Group Mapping Source

The system SHALL allow internal browser users to manually create and maintain confirmed company group mappings.

#### Scenario: User creates a confirmed group

- **GIVEN** normalized companies exist
- **WHEN** an internal browser user creates a group and adds selected companies
- **THEN** the backend SHALL persist confirmed group membership
- **AND** derived/report refresh SHALL be able to use that confirmed membership

#### Scenario: User selects an existing normalized company

- **GIVEN** company normalization already exposes company display names and WIPS codes
- **WHEN** an internal browser user creates a group or adds a group member
- **THEN** the UI SHALL provide a selector backed by the existing company registry
- **AND** the submitted company display name and code SHALL be derived from the selected registry item
- **AND** the UI SHALL NOT require the user to retype those values

### Requirement: Group Governance Changes Refresh Through SSE

The system SHALL publish committed company-group governance changes through the existing `patent_events` SSE channel so that open browser sessions can refresh the group registry without a full-page reload.

#### Scenario: Group governance data changes

- **WHEN** a manual group mutation, CLI/AI suggestion ingestion, or suggestion review is committed
- **THEN** the backend SHALL publish a `kind=data` event for resource `companyGroups`
- **AND** the event SHALL contain refresh metadata only, not company or member data
- **AND** the browser SHALL schedule a debounced group-registry refresh when the browse view is active
- **AND** reconnect compensation SHALL refresh the registry after an SSE interruption

### Requirement: CLI AI Group Suggestions Are Review Only

The system SHALL allow CLI/AI to produce group suggestions, but SHALL NOT allow CLI/AI output to become confirmed group mapping without browser user confirmation.

#### Scenario: CLI AI suggests a group

- **WHEN** CLI/AI suggests a group with members, evidence, and confidence
- **THEN** the system SHALL store or expose it as review-only suggestion data
- **AND** the suggestion SHALL NOT affect derived/report group fields until confirmed by a user

#### Scenario: User reviews CLI AI suggestions in the browser

- **GIVEN** CLI/AI has persisted review-only group suggestions with evidence
- **WHEN** an internal browser user opens group normalization
- **THEN** pending suggestions SHALL be shown separately from established groups
- **AND** the user SHALL be able to confirm or reject each suggested member
- **AND** confirming a member SHALL make its parent group confirmed for derived/report use
- **AND** established groups SHALL remain in a collapsed list by default

#### Scenario: CLI AI lacks enough evidence

- **GIVEN** there is no confirmed group seed
- **AND** there is no current user-provided target group or report goal
- **AND** there is no high-confidence internal alias or name pattern
- **WHEN** CLI/AI evaluates possible group mappings
- **THEN** it SHALL return an `insufficient_evidence` warning
- **AND** it SHALL NOT produce a confident group candidate

#### Scenario: CLI AI attempts confirmed write

- **WHEN** a CLI/AI path attempts to create or update confirmed group membership directly
- **THEN** the system SHALL reject the write
- **AND** no confirmed mapping SHALL be changed

### Requirement: Group Mapping Source Limit

The system SHALL accept only manual browser actions and CLI/AI suggestions as group mapping sources for this capability.

#### Scenario: External import is attempted

- **WHEN** an external imported group mapping source is submitted
- **THEN** the system SHALL reject it or mark it out of scope
- **AND** it SHALL NOT create confirmed group membership

#### Scenario: Future web evidence is available

- **WHEN** CLI/AI has optional web evidence for a possible group relationship
- **THEN** the evidence MAY be attached to a review-only suggestion
- **AND** it SHALL NOT create confirmed group membership
- **AND** user confirmation SHALL still be required
