## Why

Current company normalization resolves WIPS company codes and aliases into `applicant_display_name`.
Reports group by that company-level display name. This is correct for company-level analysis, but it cannot answer group-level questions such as "which patents belong to the same corporate group when multiple WIPS codes remain separate legal/company entities".

The project needs a second normalization layer above company normalization:

```text
WIPS code / alias -> normalized company -> confirmed group membership -> group-level reports
```

This change intentionally keeps company normalization and group normalization separate. A group mapping must not overwrite WIPS code, company aliases, raw patent fields, or `applicant_display_name`.

## What Changes

- Add group normalization as a company-governance capability.
- Support exactly two group mapping sources:
  - browser UI manual creation / edit / confirmation by internal users.
  - CLI/AI suggestions that are review-only until a user confirms them.
- Keep CLI/AI behavior semi-automatic: it can organize candidates and evidence, but it does not make final group decisions.
- Exclude bulk/imported external group mapping as an accepted source for this change.
- Add derived/report fields for group display names with fallback to company display names.
- Add report behavior that can run in company scope or group scope without silently changing old company-scope reports.

## Confirmed Decisions

- Group mapping sources are limited to:
  1. user manual creation in the browser UI,
  2. CLI/AI-generated suggestions.
- CLI/AI suggestions may be persisted as suggestions or workflow outputs, but they must not become confirmed group mappings without a user action.
- Confirmed mappings are written through backend APIs used by the browser UI.
- The CLI/AI role is advisory: read context, generate candidates, provide evidence/confidence, and support report planning.
- CLI/AI suggestion defaults to internal project data only. If future web access is explicitly enabled, web evidence is still evidence-only and cannot confirm or apply mappings.
- CLI/AI should only produce a candidate when it has a confirmed group seed, a user-provided target group/report goal, or a high-confidence internal alias/name pattern; otherwise it returns an `insufficient_evidence` warning.
- Do not push this work after drafting; local files only until the user asks to push or merge.

## Non-goals

- No direct CLI write to confirmed group mapping.
- No external Excel/import source for group mapping in this change.
- No automatic inference from identical Chinese company names into confirmed groups.
- No fully automatic group curation, even when external/web evidence is available.
- No mutation of raw/core patent source values.
- No change to the current company normalization semantics.
- No default replacement of existing reports from company scope to group scope.

## Impact

- DB/migration: likely add group mapping storage, or equivalent normalized persistence if an existing table can safely host it.
- Backend: add API/service for group CRUD, suggestion review, and derived refresh.
- Frontend: add a collapsed group-governance block near company normalization.
- Reports: expose group-scope variants or an explicit scope parameter.
- CLI/AI: add read-only/suggestion contract and schema.

## Activation

Requires migration, API implementation, frontend UI, derived/report refresh, and focused TDD acceptance. Existing company-scope reports remain unchanged until group scope is explicitly selected.

## Acceptance Gate

- Manual UI can create a group, add/remove normalized companies, rename group, and confirm membership.
- CLI/AI can propose group candidates with evidence and confidence, but cannot directly confirm them.
- CLI/AI returns `insufficient_evidence` instead of confident grouping when it lacks a seed, user target, or strong internal pattern.
- Confirmed group membership produces group display fields in derived/report data.
- Company-scope reports still group by company display name.
- Group-scope reports group by group display name, with fallback to company display name for ungrouped companies.
- Tests cover two different WIPS codes mapped to two different companies, then grouped under one confirmed group: company scope remains separate; group scope combines counts.
