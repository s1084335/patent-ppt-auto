## 1. Specification And Inventory

- [ ] 1.1 Red: add schema/inventory tests that fail because `derived_layer.patent_search_terms` and its required indexes do not exist.
- [ ] 1.2 Record current migration-declared indexes and the planned search indexes in `docs/db_index_governance.md`.
- [ ] 1.3 Add Supabase `pg_indexes` and representative `EXPLAIN` capture steps to the governance document; mark live results pending until deployment access is available.

## 2. TDD: Search Term Refresh

- [ ] 2.1 Red: create refresh tests proving multi-value applicants, owners, assignees, inventors, and IPC/CPC All fields generate separate search terms.
- [ ] 2.2 Green: add migration, refresh function, `pg_trgm`, constraints, and indexes for `derived_layer.patent_search_terms`.
- [ ] 2.3 Red: prove duplicate source values do not create duplicate `(patent_id, field_key, term_lookup)` rows.
- [ ] 2.4 Green: make refresh idempotent and connect it after `report_patent_base` refresh.

## 3. TDD: Browse Search Contract

- [ ] 3.1 Red: API tests for `/patents?keyword=...` find a non-primary applicant, owner, assignee, inventor, and IPC/CPC All token.
- [ ] 3.2 Red: API tests for `/workspaces/{id}/patents?keyword=...` use the same search-term contract and do not return duplicate patents.
- [ ] 3.3 Red: topic patent keyword filtering, where supported, uses the same search-term contract.
- [ ] 3.4 Green: update shared patent/workspace query predicates to search through `patent_search_terms`.
- [ ] 3.5 Refactor: keep one search predicate helper/source so future routes do not reimplement field lists.

## 4. Acceptance And Deployment Checks

- [ ] 4.1 Run focused tests for migration/schema, search refresh, patent browse API, workspace browse API, and topic browse API.
- [ ] 4.2 Run `openspec validate add-patent-search-terms-and-db-index-governance --strict`.
- [ ] 4.3 On Supabase, verify `pg_trgm`, table, unique constraint, and the three required search indexes exist.
- [ ] 4.4 On Supabase, run representative `EXPLAIN` for a participant search and record whether the trigram index is used or why the planner chose another path.
- [ ] 4.5 Confirm no existing index was dropped by this change.

