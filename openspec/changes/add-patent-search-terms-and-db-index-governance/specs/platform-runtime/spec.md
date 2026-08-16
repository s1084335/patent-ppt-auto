## ADDED Requirements

### Requirement: Database API Hot Paths Are Index-Governed

The system SHALL maintain an index-governance inventory for database-backed API,
worker, and MCP hot paths so new indexes are connected to real query behavior.

#### Scenario: Hot path inventory maps queries to indexes

- **GIVEN** a database-backed API, worker, or MCP path is part of normal product
  operation
- **WHEN** the path reads, writes, joins, filters, orders, claims, or cleans up
  database rows
- **THEN** the governance record SHALL list its route or repository function,
  main tables, filter/join/order columns, expected index, and index purpose
- **AND** the path SHALL be marked as `uses_existing_index`, `needs_new_index`,
  `observe`, or `no_index_rationale`

#### Scenario: Browse keyword paths are connected to search-term indexes

- **GIVEN** the search-term table and indexes are available
- **WHEN** global browse, workspace browse, or keyword-capable topic browse
  filters by keyword
- **THEN** the query SHALL use the shared search-term predicate
- **AND** the governance record SHALL include representative `EXPLAIN`
  acceptance for the search-term index path

#### Scenario: Non-search hot paths are not silently ignored

- **GIVEN** workflow run claim/list, report artifact lookup, workspace membership,
  company/group normalization, import blob cleanup, or MCP evidence lookup is
  present
- **WHEN** this change is implemented
- **THEN** each path SHALL be classified in the governance record
- **AND** any unimplemented index improvement SHALL be recorded as a later
  candidate rather than silently omitted

#### Scenario: Index additions require a query owner

- **WHEN** an index is added or proposed
- **THEN** it SHALL name at least one owning query path and purpose category
- **AND** indexes SHALL NOT be added only because a column may be queried someday
