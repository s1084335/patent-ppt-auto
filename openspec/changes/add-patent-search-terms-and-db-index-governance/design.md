# Design: Patent Search Terms And DB Index Governance

## Context

Current browse search uses a narrow `ILIKE` predicate over patent number, title,
and `applicant_display_name`. Maintenance code already proves that WIPS people
fields can contain ` | ` separated values and that those values must be split for
company-name governance. Reporting also has an applicant-expanded view, but that
view is report-specific and only covers applicants.

This change introduces a general derived search layer so search behavior and DB
index choices have one owner.

## Data Model

Add `derived_layer.patent_search_terms`.

Minimum columns:

- `patent_id BIGINT NOT NULL`
- `field_key TEXT NOT NULL`
- `field_label TEXT NOT NULL`
- `term_text TEXT NOT NULL`
- `term_lookup TEXT NOT NULL`
- `is_primary BOOLEAN NOT NULL DEFAULT false`
- `source_rank INTEGER NOT NULL DEFAULT 100`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

Minimum constraints and indexes:

- FK from `patent_id` to `core_layer.patents(id)` with cascade delete.
- Unique index on `(patent_id, field_key, term_lookup)`.
- Btree index on `patent_id`.
- Btree index on `(field_key, term_lookup)` for exact governance/debug lookup.
- GIN trigram index on `term_lookup` for `%keyword%` browse search.

Migration must enable `pg_trgm` with `CREATE EXTENSION IF NOT EXISTS pg_trgm`.

## Search Term Sources

The refresh populates search terms from the explicit user-searchable patent
field allowlist. The allowlist is the contract for coverage: any patent-data
field that users may reasonably type into browse keyword search, and that can
contain WIPS ` | ` separated values, must be included or explicitly documented
as excluded.

The initial required allowlist includes:

- patent identifiers exposed by the existing patent-number display/fallback
  logic,
- title, original title, abstract, original abstract,
- country code, legal status, patent type, document kind,
- applicant display name, current owner display name, recent assignee display
  name,
- raw applicant, standardized applicant, recent patent owner, standardized
  current owner, recent assignee, inventor,
- IPC/CPC Main and All fields that are available in core/report projection.

This allowlist is intentionally broader than the current browse predicate, but
it is still product-owned. It must not become a blind dump of every
`patent_attributes` or unused source column; fields outside the allowlist need a
reason to be searchable.

WIPS ` | ` separated fields must be split with the same semantics as
`split_multi_value`: trim parts, drop empty parts, keep original text for
`term_text`, and normalize lookup by trimming, lowercasing, and collapsing
whitespace.

Primary display/report behavior remains separate:

- `report_patent_base` may continue to use primary applicant/owner/assignee for
  display and aggregation.
- `patent_search_terms` may include all participants for search.

## Query Contract

All patent keyword browse routes use the same predicate:

```sql
EXISTS (
  SELECT 1
  FROM derived_layer.patent_search_terms st
  WHERE st.patent_id = candidates.patent_id
    AND st.term_lookup ILIKE %(kw_lookup)s
)
```

The result rows remain patent rows. Search term matches must not multiply result
rows; use `EXISTS` or `DISTINCT` at the search boundary.

Routes in scope:

- `GET /api/v1/patents?keyword=...`
- `GET /api/v1/workspaces/{workspace_id}/patents?keyword=...`
- topic patent listing routes that expose keyword filtering.

`GET /api/v1/patents/search` may remain a patent-number quick search unless the
implementer explicitly routes it through the same contract without changing its
public response shape.

## MCP Evidence Access

Read-only MCP evidence access must be aligned with the same index governance
record. The purpose is not to remove `query_database`; it is to prevent CLI
report generation from bypassing indexed routes after they exist.

Required evidence-route mapping:

- report-row evidence: use report snapshot/report rows and the indexes recorded
  for report artifact or report aggregation lookups,
- patent-id evidence: use patent-id keyed lookups, not keyword scans,
- company/group evidence: use normalized company/group lookup paths and their
  recorded lookup indexes,
- keyword-style patent discovery: use `derived_layer.patent_search_terms` or a
  typed API/helper that uses the same predicate as browse search.

CLI-facing prompt examples and MCP tool documentation must not teach agents to
query raw patent/people wide tables with broad multi-column `ILIKE` predicates
when an indexed evidence route exists. Free-form `query_database` remains
available for exceptional read-only investigation, but representative SQL in the
governance document must show the indexed form first.

## Database API Hot Paths

The governance record must inventory database-backed API and worker hot paths,
not only the new search table. Each row must identify the route or repository
function, the main tables, filter/join/order columns, the expected index, and
whether this change modifies the query.

Minimum hot paths to classify:

- global patent browse keyword and pagination,
- workspace patent browse keyword and pagination,
- keyword-capable topic patent listing,
- MCP report evidence, company/group evidence, topic evidence, and patent-id
  evidence lookups,
- workflow run create/get/list/claim/requeue paths,
- report artifact version/file lookups,
- workspace membership JSONB expansion or equivalent membership lookup,
- company alias/group normalization lookup and confirmation paths,
- import blob cleanup and active-job reference checks.

This change must connect the browse keyword paths to `patent_search_terms`.
Other hot paths do not all need query rewrites in this change, but each must be
documented as `uses_existing_index`, `needs_new_index`, `observe`, or
`no_index_rationale`. Any `needs_new_index` item that is not required for the
search-term contract should be planned for a later user-approved change unless
it is low-risk and directly required by this change.

## Refresh Integration

Search terms are rebuilt as part of derived refresh after the source projections
they depend on are current. The expected order is:

1. refresh `report_patent_base`,
2. refresh group/report projections when needed,
3. refresh `patent_search_terms`.

Import completion, company-name/group confirmation, and TW legal-status changes
already enqueue derived/report refresh work; those paths must refresh search
terms before users rely on browse search.

## Index Governance

Add `docs/db_index_governance.md` as the persistent index record.

The document must include:

- index inventory from migrations,
- Supabase `pg_indexes` inventory when live DB access is available,
- hot query route ownership,
- index purpose categories: `constraint`, `lookup`, `pagination_order`,
  `job_claim`, `report_aggregation`, `search`, `foreign_key_join`,
- MCP evidence route ownership and API/repository hot-path mapping,
- keep/observe/drop-candidate status,
- acceptance SQL for `pg_indexes` and representative `EXPLAIN`.

This change only adds required search indexes and governance documentation. It
must not drop existing indexes.

## Risks

- A broad substring search can become slow without trigram index support.
- Splitting all multi-value fields changes search recall, not display semantics;
  tests must lock that distinction.
- Refresh order mistakes can make a just-confirmed company or legal status
  searchable only after a later refresh.
