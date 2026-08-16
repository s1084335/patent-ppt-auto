# Active OpenSpec Delivery Router

Last updated: 2026-08-17

This file is the working router for active OpenSpec changes. Agents should use it
before picking up any `openspec/changes/*` work so the remaining work is handled
as delivery lines, not as unrelated parallel TODO lists.

## Global rules for agents

- Clarify unclear requirements with the user before planning.
- Plan and update OpenSpec first, then implement with TDD.
- Validate with the project's module and composed acceptance gates before merge.
- Work on a branch, push the branch, and merge to `master` only after the gate
  passes or the user explicitly accepts the risk.
- Do not create a new change for small follow-up items when an existing routed
  change owns the behavior.
- Do not modify `backend/app/clustering/` or replace the clustering model unless
  the user explicitly reopens P3.

## Frozen / deferred work

### P3 clustering model

`replace-clustering-with-dpmeans` is frozen. The clustering model is not part of
the current delivery batch. Agents must not implement it, tune it, or treat its
unchecked tasks as active work.

### Historical taxonomy route

Archived taxonomy work under `openspec/changes/archive/taxonomy-v0` is historical
context only. It is not active implementation scope.

## Current delivery lines

### 1. Deck delivery acceptance closeout

Primary changes:

- `add-deck-delivery-line`
- `deepen-deck-evidence-layer`

Goal:

Finish report/deck generation acceptance with evidence-grounded narrative,
readable Chinese output labels, and no generic filler text.

Scope note:

This line owns deck/report-output ready acceptance only. Other ready branches,
such as company/group normalization validation, stay in their routed delivery
line and must not be folded into deck delivery.

How to work:

- Treat `add-deck-delivery-line` as the delivery shell and acceptance owner.
- Treat `deepen-deck-evidence-layer` as the evidence/data-caliber owner.
- Do not re-open completed deck sections just to move work between changes.
- Do not introduce ungrounded narrative strings.

Ready gate:

- Targeted module tests for changed report/deck components pass.
- `openspec validate add-deck-delivery-line --strict` passes.
- `openspec validate deepen-deck-evidence-layer --strict` passes.
- Real artifact or deployment acceptance is ready for the user to verify.

### 2. Platform reliability

Primary changes:

- `harden-runtime-security-and-configuration`
- `show-honest-progress-for-long-ai-tasks`
- `establish-quality-automation`

Goal:

Make runtime behavior honest and operable: readiness, SSE/progress, security
configuration, and quality gates should expose real status instead of silent or
misleading states.

How to work:

- Clean unresolved Open Questions in `establish-quality-automation` before
  implementation.
- Keep SSE/job-status wiring centralized instead of scattering ad hoc polling.
- Preserve Supabase as the database boundary unless a change explicitly says
  otherwise.

Ready gate:

- Targeted tests cover readiness/progress/security changes.
- Quality automation commands are documented and callable by agents.
- Deployment verification includes `/api/v1/health`, `/api/v1/ready`, task list,
  and backend/worker logs.

### 3. File and storage lifecycle

Primary changes:

- `move-import-uploads-to-object-storage`
- `implement-retention-archive`

Goal:

Move file-like data toward the NAS plus MinIO/S3-compatible product direction and
define retention behavior for imports, generated artifacts, drafts, and delivered
outputs.

How to work:

- Treat local filesystem folders that emulate NAS/object storage as development
  substitutes, not final product storage.
- Keep PostgreSQL/Supabase as structured metadata storage.
- Do not delete existing artifacts without explicit user approval.

Ready gate:

- Storage paths, object keys, and retention policies are documented.
- Cleanup jobs are testable without touching production-like retained data.
- Draft retention period and "mark as delivered" entry point are explicit.

### 4. Import and three-zone E2E

Primary changes:

- `harden-import-formats`
- `complete-three-zone-e2e-acceptance`

Goal:

Make import behavior, normalized data, analysis, and the three-zone UI acceptance
work as one product path.

How to work:

- Preserve patent identifiers through importer, DB mappings, derived data, and
  reports.
- Treat Supabase schema and live importer behavior as evidence to inspect before
  explaining failures.
- Keep validation data separate from destructive cleanup.

Ready gate:

- Import format tests pass for supported real files.
- Three-zone E2E verifies workspace load, patent browsing, analysis/report
  surfaces, and AI assistant/job status behavior.

### 5. Frontend governance and data consistency

Primary changes:

- `add-batch-exclusion-review`
- `add-frontend-snapshot-cache`

Goal:

Keep user-facing review controls and cached frontend snapshots consistent with
current database/report state.

How to work:

- Prefer compact list/table views when dense result sets would push charts or
  core content below the fold.
- Keep manual user decisions auditable through existing data structures unless a
  change explicitly introduces new storage.

Ready gate:

- Frontend state refreshes after background operations without stealing focus.
- Snapshot/cache behavior cannot show stale decisions as current facts.

### 6. Company/group normalization validation tail

Primary change:

- `add-company-group-normalization`

Goal:

Close remaining validation for company/group normalization after deployment is
available.

How to work:

- Treat this as validation/acceptance tail, not a new feature build.
- AI/CLI may suggest normalization, but user confirmation writes to DB.
- AI must not generate WIPS company codes.

Ready gate:

- Branch/deployment validation confirms company suggestions, existing-group
  linking, confirmation writes, revert/removal behavior, and SSE/job progress.
