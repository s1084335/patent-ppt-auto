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

### Requirement: CLI AI Group Suggestions Are Review Only

The system SHALL allow CLI/AI to produce group suggestions, but SHALL NOT allow CLI/AI output to become confirmed group mapping without browser user confirmation.

#### Scenario: CLI AI suggests a group

- **WHEN** CLI/AI suggests a group with members, evidence, and confidence
- **THEN** the system SHALL store or expose it as review-only suggestion data
- **AND** the suggestion SHALL NOT affect derived/report group fields until confirmed by a user

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
