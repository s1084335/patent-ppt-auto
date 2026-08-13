---
name: company-group-normalization
description: Guide product and CLI agents to semi-automatically curate company group mappings above WIPS/company normalization in the patent project. Use when generating, reviewing, or applying company group suggestions, distinguishing company-scope versus group-scope patent reports, or changing group normalization UI/API/CLI workflows.
---

# Company Group Normalization

## Overview

Use this skill when working on group normalization for patent reports. Group normalization is a second layer above company normalization: it groups already-normalized companies into a corporate group for explicit group-scope analysis.

Do not replace WIPS company codes, `company_aliases`, raw patent data, or company-level display names.

## Core Contract

Follow this data flow:

```text
WIPS code / alias -> normalized company -> confirmed group membership -> explicit group-scope reports
```

Keep these scopes separate:

- Company-scope reports group by company display fields such as `applicant_display_name`.
- Group-scope reports group by group display fields such as `applicant_group_display_name`.
- Suggested mappings never affect reports until an internal browser user confirms them.

## Allowed Sources

Use only these sources:

1. Manual browser user actions: create, rename, add/remove members, confirm suggestions, reject suggestions.
2. CLI/AI suggestions: review-only candidates with evidence, confidence, and warnings.

CLI/AI must stay semi-automatic. Public-web evidence may support a suggestion, but it must not create or confirm a group mapping.

## CLI/AI Suggestion Rules

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

The backend must reject unknown company codes/names and members without an HTTPS evidence URL.
Persist accepted output only through the existing suggestion repository. SSE completion refreshes
the browser review section; when there are no suggestions, the whole section stays hidden.

Generate a candidate only when at least one basis exists:

- a confirmed group seed,
- a user-provided target group,
- a high-confidence internal alias/name pattern.
- verifiable public-web evidence gathered by the manual suggestion job.

If no basis exists, return `insufficient_evidence` and do not propose a confident group. Identical Chinese display names alone are not enough to confirm ownership or group relationship.

Minimum suggestion payload:

```json
{
  "suggested_group_name": "Example Group",
  "members": [
    {
      "company_code": "A001",
      "company_display_name": "Example Company"
    }
  ],
  "confidence": "low|medium|high",
  "evidence": [
    {
      "type": "alias|name_similarity|existing_mapping|report_context|user_target",
      "value": "..."
    }
  ],
  "warnings": [
    "insufficient_evidence"
  ]
}
```

Use warning flags such as:

- `insufficient_evidence`
- `same_name_different_code`
- `brand_or_subsidiary_uncertain`
- `external_evidence_required`

## Write Boundaries

CLI/AI may write only through backend-controlled suggestion workflows or output artifacts. It must not:

- create `confirmed` group rows,
- change confirmed memberships,
- delete groups,
- rename groups,
- mutate raw/core patent or company source data,
- silently change the default report scope.

Manual confirmation through the browser/backend is required before derived data or reports use a group mapping.

## Implementation Pointers

Treat `openspec/changes/add-company-group-normalization/` as the authoritative product specification until the change is implemented and archived. Expected persistence is a group table plus a group membership table, such as `derived_layer.company_groups` and `derived_layer.company_group_members`, unless implementation finds an equivalent schema that preserves the same contract.
