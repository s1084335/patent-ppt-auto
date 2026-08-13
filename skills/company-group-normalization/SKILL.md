---
name: company-group-normalization
description: Guide product and CLI agents to semi-automatically curate company group mappings above WIPS/company normalization in the patent project. Use when generating, reviewing, or applying company group suggestions, distinguishing company-scope versus group-scope patent reports, or changing group normalization UI/API/CLI workflows.
---

# Company Group Normalization

## 執行 Runbook

### Overview

Use this skill when working on group normalization for patent reports. Group normalization is a second layer above company normalization: it groups already-normalized companies into a corporate group for explicit group-scope analysis.

Do not replace WIPS company codes, `company_aliases`, raw patent data, or company-level display names.

### Core Contract

Follow this data flow:

```text
WIPS code / alias -> normalized company -> confirmed group membership -> explicit group-scope reports
```

Keep these scopes separate:

- Company-scope reports group by company display fields such as `applicant_display_name`.
- Group-scope reports group by group display fields such as `applicant_group_display_name`.
- Suggested mappings never affect reports until an internal browser user confirms them.

### Allowed Sources

Use only these sources:

1. Manual browser user actions: create, rename, add/remove members, confirm suggestions, reject suggestions.
2. CLI/AI suggestions: review-only candidates with evidence, confidence, and warnings.

CLI/AI must stay semi-automatic. Public-web evidence may support a suggestion, but it must not create or confirm a group mapping.

### CLI/AI Suggestion Rules

The product may perform public-web research only through the centralized manual
`ai:company_group_suggestion` job. Page load and import must never start it automatically.
The CLI receives a backend-controlled candidate list and exactly these tools:

- `WebSearch`
- `WebFetch`

Do not grant Bash, file tools, MCP tools, database credentials, or direct database access.
Use Claude for this job because the current OpenCode gateway cannot enforce the tool allowlist.

Use these controlled inputs:

- existing confirmed group mappings,
- current user-provided target group or report goal,
- `company_aliases`,
- WIPS company codes, aliases, and normalized display names,
- current report context and high-impact ungrouped rows.

The backend must reject unknown company codes/names, unknown `target_group_id` values, and members without an HTTPS evidence URL.
Persist accepted output only through the existing suggestion repository. SSE completion refreshes
the browser review section; when there are no suggestions, the whole section stays hidden.

The backend supplies confirmed groups and their confirmed seed members as a controlled list. The
CLI may either target one of those groups by `target_group_id` or propose a new `group_name`. An
existing target is revalidated during persistence, and its name is never taken from model output.

Generate a candidate only when at least one basis exists:

- a confirmed group seed,
- a user-provided target group,
- a high-confidence internal alias/name pattern.
- verifiable public-web evidence gathered by the manual suggestion job.

If no basis exists, return `insufficient_evidence` and do not propose a confident group. Identical Chinese display names alone are not enough to confirm ownership or group relationship.

Minimum existing-group suggestion payload:

```json
{
  "target_group_id": 123,
  "members": [
    {
      "company_code": "A001",
      "company_display_name": "Example Company",
      "evidence_json": {
        "confidence": "high",
        "sources": [{
          "url": "https://example.com/evidence",
          "title": "Evidence title",
          "claim": "Evidence summary"
        }],
        "warnings": []
      }
    }
  ]
}
```

For a new group, replace `target_group_id` with `group_name`. Existing-group review displays the
target name read-only; only a new pending group may be renamed during confirmation.

Use warning flags such as:

- `insufficient_evidence`
- `same_name_different_code`
- `brand_or_subsidiary_uncertain`
- `external_evidence_required`

### Write Boundaries

CLI/AI may write only through backend-controlled suggestion workflows or output artifacts. It must not:

- create `confirmed` group rows,
- change confirmed memberships,
- delete groups,
- rename groups,
- mutate raw/core patent or company source data,
- silently change the default report scope.

Manual confirmation through the browser/backend is required before derived data or reports use a group mapping.

## 開發備註

### Implementation Pointers

Treat `openspec/specs/company-governance/` as the authoritative implemented product specification.
Persistence uses `derived_layer.company_groups` and `derived_layer.company_group_members`;
do not add a parallel group mapping source.

The existing-group extension is specified by `openspec/changes/suggest-existing-company-group-membership/` until implementation and archive.
