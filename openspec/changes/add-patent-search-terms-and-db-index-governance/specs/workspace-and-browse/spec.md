## ADDED Requirements

### Requirement: Patent Browse Keyword Search Uses Expanded Search Terms

The system SHALL use a derived patent search-term layer for patent browse
keyword filtering so values inside WIPS multi-value fields can match individual
patents.

#### Scenario: Global browse finds a non-primary applicant

- **GIVEN** a patent has applicant text `Alpha Corp | Beta Corp`
- **WHEN** a user searches global patent browse for `Beta Corp`
- **THEN** the patent SHALL be included in the result
- **AND** the patent SHALL appear only once

#### Scenario: Workspace browse finds a non-primary owner or assignee

- **GIVEN** a workspace contains a patent with owner text `Owner A | Owner B`
- **OR** assignee text `Assignee A | Assignee B`
- **WHEN** a user searches the workspace patent list for `Owner B` or `Assignee B`
- **THEN** the patent SHALL be included in the result
- **AND** the patent SHALL appear only once

#### Scenario: Browse finds inventor and classification values

- **GIVEN** a patent has an inventor or IPC/CPC All value that is not represented
  by `applicant_display_name`
- **WHEN** a user searches browse by that inventor or classification token
- **THEN** the patent SHALL be included in the result

#### Scenario: Search does not change display fields

- **GIVEN** a patent is found through a non-primary search term
- **WHEN** the result row is rendered
- **THEN** the row SHALL still use the existing patent display fields
- **AND** the API SHALL NOT expand search terms into duplicate rows

### Requirement: Shared Browse Search Predicate

The system SHALL keep global browse, workspace browse, and keyword-capable topic
patent browse on one shared search-term predicate.

#### Scenario: Same keyword across browse scopes

- **GIVEN** the same patent is visible in global browse and a workspace browse
- **WHEN** the user searches the same non-primary participant keyword in both
  scopes
- **THEN** both scopes SHALL apply the same normalization and matching behavior
