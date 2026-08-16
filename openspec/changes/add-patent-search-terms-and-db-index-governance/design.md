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

The refresh populates search terms from:

- patent identifiers exposed by the existing patent-number display/fallback
  logic,
- title, original title, abstract, original abstract,
- country code, legal status, patent type, document kind,
- applicant display name, current owner display name, recent assignee display
  name,
- raw applicant, standardized applicant, recent patent owner, standardized
  current owner, recent assignee, inventor,
- IPC/CPC Main and All fields that are available in core/report projection.

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

