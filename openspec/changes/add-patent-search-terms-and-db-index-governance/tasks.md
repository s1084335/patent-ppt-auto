## 1. Specification And Inventory

- [x] 1.1 Red: add schema/inventory tests that fail because `derived_layer.patent_search_terms` and its required indexes do not exist.
- [x] 1.2 Record current migration-declared indexes and the planned search indexes in `docs/db_index_governance.md`.
- [x] 1.3 Add Supabase `pg_indexes` and representative `EXPLAIN` capture steps to the governance document; mark live results pending until deployment access is available.
- [x] 1.4 Define the user-searchable patent field allowlist for search terms, including every searchable WIPS ` | ` multi-value field and an explicit exclusion reason for omitted raw/attribute fields.
- [x] 1.5 Inventory MCP evidence tools and DB-backed API/repository hot paths in the governance record, with expected index, status, and no-index rationale where applicable.

## 2. TDD: Search Term Refresh

- [x] 2.1 Red: create refresh tests proving every allowlisted multi-value field generates separate search terms, including applicants, owners, assignees, inventors, and IPC/CPC All fields.
- [x] 2.2 Green: add migration, refresh function, `pg_trgm`, constraints, and indexes for `derived_layer.patent_search_terms`.
- [x] 2.3 Red: prove duplicate source values do not create duplicate `(patent_id, field_key, term_lookup)` rows.
- [x] 2.4 Green: make refresh idempotent and connect it after `report_patent_base` refresh.

## 3. TDD: Browse Search Contract

- [x] 3.1 Red: API tests for `/patents?keyword=...` find a non-primary applicant, owner, assignee, inventor, and IPC/CPC All token.
- [x] 3.2 Red: API tests for `/workspaces/{id}/patents?keyword=...` use the same search-term contract and do not return duplicate patents.
- [x] 3.3 Red: topic patent keyword filtering, where supported, uses the same search-term contract.
- [x] 3.4 Green: update shared patent/workspace query predicates to search through `patent_search_terms`.
- [x] 3.5 Refactor: keep one search predicate helper/source so future routes do not reimplement field lists.

## 4. TDD: MCP And API Index Alignment

- [x] 4.1 Red: add contract/static tests proving CLI-facing MCP data-access guidance does not recommend raw wide-table keyword scans when an indexed search-term or evidence route exists.
- [x] 4.2 Green: update MCP data-access guidance and examples so keyword-style patent discovery uses `patent_search_terms` or the shared browse predicate.
- [x] 4.3 Red: add governance tests or structured checks proving each listed API/MCP hot path has an index status: `uses_existing_index`, `needs_new_index`, `observe`, or `no_index_rationale`.
- [x] 4.4 Green: complete the hot-path inventory for browse, workspace, topic, MCP evidence, workflow runs, report artifacts, workspace membership, company/group normalization, and import blob cleanup.

## 5. Acceptance And Deployment Checks

- [x] 5.1 Run focused tests for migration/schema, search refresh, patent browse API, workspace browse API, topic browse API, MCP data-access guidance, and index-governance structured checks.
- [x] 5.2 Run `openspec validate add-patent-search-terms-and-db-index-governance --strict`.
- [ ] 5.3 On Supabase, verify `pg_trgm`, table, unique constraint, and the three required search indexes exist.
- [ ] 5.4 On Supabase, run representative `EXPLAIN` for a participant search and record whether the trigram index is used or why the planner chose another path.
- [ ] 5.5 On Supabase or an equivalent PostgreSQL environment, capture representative `EXPLAIN` or rationale for MCP evidence and DB API hot paths listed in the governance record.
- [x] 5.6 Confirm no existing index was dropped by this change.
