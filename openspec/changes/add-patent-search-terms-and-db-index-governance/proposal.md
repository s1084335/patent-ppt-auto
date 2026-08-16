## Why

The browse/search path currently filters patents by patent number, title, and
`applicant_display_name`. That misses values stored in multi-value WIPS fields
such as `A | B | C`, including non-primary applicants, patent owners,
assignees, inventors, and multi-value classification fields.

The database also has many useful indexes in migrations, but no single
governance record that ties each index to a hot query path, constraint, or
retention decision. New search work needs an index plan that is explicit enough
for future agents to extend without guessing.

## What Changes

- Add a derived search layer, `derived_layer.patent_search_terms`, as the single
  searchable-token source for patent browse/search.
- Populate search terms from patent identifiers, title, abstract, country/legal
  fields, report display names, people/company fields, inventors, and IPC/CPC
  Main/All classification fields.
- Split WIPS multi-value text with the existing ` | ` semantics so searching any
  participant or classification token can find the patent.
- Route `/patents?keyword=...`, `/workspaces/{id}/patents?keyword=...`, and topic
  patent keyword filtering through the same search-term contract.
- Add database index governance documentation that records current migration
  indexes, Supabase-observed indexes when available, hot query ownership, and
  the reason for each new search index.

## Confirmed Decisions

- Raw/core patent fields are not rewritten, split, or overwritten.
- Existing `report_patent_base` display/report aggregation semantics remain
  unchanged. Search is a separate derived layer.
- PostgreSQL/Supabase is the indexing target for this change.
- `pg_trgm` may be enabled by migration for substring search.
- P3 clustering model work remains frozen. Do not implement, tune, or refactor
  clustering model logic as part of this change.
- This change may add search/index migrations and tests, but must not delete
  existing indexes without a separate user-approved change.

## Non-goals

- No change to clustering algorithms or `backend/app/clustering/` model logic.
- No replacement of company/group normalization decisions.
- No report aggregation change from primary-value semantics to all-participant
  semantics.
- No deletion of existing database indexes.
- No frontend redesign beyond keeping the existing keyword input behavior.

## Impact

- DB/migration: add `derived_layer.patent_search_terms`, refresh path, and
  supporting indexes.
- Backend: update patent list/search SQL to use search terms without duplicating
  result rows.
- Frontend: no new control is required; existing keyword search should gain
  broader matching.
- Documentation: add a database index governance record and acceptance procedure.

## Activation

Requires Alembic migration, derived refresh integration, focused API/search
tests, migration/schema tests, OpenSpec validation, and Supabase index/EXPLAIN
acceptance before merge.

## Acceptance Gate

- Searching a non-primary applicant, patent owner, assignee, inventor, or IPC/CPC
  All token returns the patent in global browse, workspace browse, and topic
  patent browse where that route accepts keyword filtering.
- Searching first and later multi-value participants returns each patent only
  once.
- Existing display/report fields keep their current primary-value behavior.
- `patent_search_terms` contains no duplicate `(patent_id, field_key,
  term_lookup)` rows.
- Required search indexes exist in Supabase and are tied to documented query
  paths.
- Representative search SQL has an `EXPLAIN` record showing the intended
  search-term index path or a documented reason when a planner chooses otherwise.
- No existing index is dropped in this change.

