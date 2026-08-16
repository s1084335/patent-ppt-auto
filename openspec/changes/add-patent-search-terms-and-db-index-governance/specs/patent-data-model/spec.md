## ADDED Requirements

### Requirement: Derived Patent Search Terms

The system SHALL maintain `derived_layer.patent_search_terms` as the single
derived source for patent browse keyword search terms.

#### Scenario: User-searchable multi-value patent fields are inventoried

- **GIVEN** patent data contains fields that users may reasonably search from the
  browse keyword input
- **WHEN** a field can contain WIPS ` | ` separated values
- **THEN** the field SHALL be included in the searchable-field allowlist
- **OR** it SHALL be documented as intentionally excluded with a product reason
- **AND** the allowlist SHALL be covered by search-term refresh tests

#### Scenario: Multi-value people and company fields are split for search

- **GIVEN** raw/core patent fields contain WIPS ` | ` separated people or company
  names
- **WHEN** derived search terms are refreshed
- **THEN** each non-empty trimmed part SHALL be represented as a searchable term
- **AND** the original raw/core field SHALL remain unchanged

#### Scenario: Search terms include non-people patent fields

- **GIVEN** a patent has identifiers, title, abstract, country/legal fields, or
  IPC/CPC Main/All classification values
- **WHEN** derived search terms are refreshed
- **THEN** those values SHALL be represented as searchable terms when non-empty

#### Scenario: Unused raw attributes are not automatically searchable

- **GIVEN** a raw source column is stored only as unused patent attribute data
- **WHEN** it is not in the user-searchable field allowlist
- **THEN** the search-term refresh SHALL NOT index it by default
- **AND** adding it to browse search SHALL require an explicit allowlist update

#### Scenario: Search term refresh is idempotent

- **GIVEN** search terms have already been generated for a patent
- **WHEN** the search-term refresh runs again without source data changes
- **THEN** it SHALL NOT create duplicate `(patent_id, field_key, term_lookup)` rows

### Requirement: Search Term Indexes

The system SHALL create database indexes that support patent search-term lookup
and make their purpose auditable.

#### Scenario: Search indexes exist

- **WHEN** migrations are applied
- **THEN** `derived_layer.patent_search_terms` SHALL have a patent join index
- **AND** it SHALL have a `(field_key, term_lookup)` lookup index
- **AND** it SHALL have a trigram GIN index on `term_lookup`
- **AND** it SHALL have a unique constraint or unique index preventing duplicate
  terms per patent and field

#### Scenario: Existing indexes are not removed

- **WHEN** this change is applied
- **THEN** existing database indexes SHALL NOT be dropped
- **AND** any drop-candidate index SHALL only be documented for a future
  user-approved change

### Requirement: Database Index Governance Record

The system SHALL maintain a database index governance record that ties indexes
to query paths and acceptance checks.

#### Scenario: New search index is documented

- **WHEN** a search-related index is added
- **THEN** the governance record SHALL describe its query path, purpose category,
  and verification SQL
