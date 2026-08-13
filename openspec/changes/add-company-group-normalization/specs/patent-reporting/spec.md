## ADDED Requirements

### Requirement: Explicit Company Or Group Report Scope

The system SHALL keep company-scope report aggregation separate from group-scope report aggregation.

#### Scenario: Company-scope reports remain unchanged

- **GIVEN** two normalized companies are confirmed under one group
- **WHEN** a company-scope applicant report is generated
- **THEN** the report SHALL group by company display name
- **AND** the two companies SHALL remain separate rows when their company display names differ

#### Scenario: Group-scope reports combine confirmed group members

- **GIVEN** two normalized companies are confirmed under one group
- **WHEN** a group-scope applicant report is generated
- **THEN** the report SHALL group by group display name
- **AND** patent counts for both companies SHALL be combined under that group row

#### Scenario: Ungrouped company fallback

- **GIVEN** a normalized company has no confirmed group mapping
- **WHEN** a group-scope applicant report is generated
- **THEN** the group display name SHALL fallback to the company display name

### Requirement: Report Scope Is Visible

The system SHALL label report data, charts, exported HTML, and CLI/AI report planning evidence with the active aggregation scope.

#### Scenario: Group report is viewed

- **WHEN** a user views or exports a group-scope report
- **THEN** the UI/output SHALL indicate group scope
- **AND** it SHALL NOT imply the numbers are company-scope numbers
