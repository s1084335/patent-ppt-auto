## ADDED Requirements

### Requirement: MCP Evidence Queries Use Indexed Data Routes

The system SHALL guide read-only MCP evidence gathering toward indexed report,
patent, company/group, topic, and search-term routes instead of raw wide-table
keyword scans when an indexed route exists.

#### Scenario: Keyword-style patent discovery uses search terms

- **GIVEN** an AI companion needs to find patents by applicant, owner, assignee,
  inventor, or classification text
- **WHEN** an indexed `derived_layer.patent_search_terms` route exists
- **THEN** the CLI-facing guidance SHALL prefer that route or a typed helper
  using the shared browse predicate
- **AND** it SHALL NOT recommend broad raw patent/people multi-column `ILIKE`
  scans as the default method

#### Scenario: Direct patent evidence uses patent identifiers

- **GIVEN** an AI companion already has one or more `patent_id` values
- **WHEN** it requests patent-level evidence
- **THEN** the MCP route SHALL use patent-id keyed lookup semantics
- **AND** the index-governance record SHALL identify the expected lookup index
  or a no-index rationale

#### Scenario: Company and group evidence use normalized lookup paths

- **GIVEN** an AI companion needs company or group evidence
- **WHEN** normalized company/group tables or projections contain the requested
  entity
- **THEN** the MCP route SHALL use the normalized lookup path
- **AND** the index-governance record SHALL identify the expected lookup index
  or a no-index rationale

#### Scenario: Free-form query remains exceptional and auditable

- **GIVEN** `query_database` remains available for read-only investigation
- **WHEN** an equivalent typed or indexed evidence route exists
- **THEN** CLI-facing examples SHALL show the indexed route first
- **AND** any representative free-form SQL in governance SHALL include an
  `EXPLAIN` check or documented reason why an index is not expected
