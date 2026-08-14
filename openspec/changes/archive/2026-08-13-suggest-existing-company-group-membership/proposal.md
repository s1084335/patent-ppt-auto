# Change: Suggest membership in an existing company group

## Why

The company-group AI job currently receives only ungrouped companies. It cannot identify an
existing confirmed group as the target, and ingestion always creates a new suggested parent group.
New subsidiaries therefore require manual matching even when a confirmed group already exists.

## What Changes

- Provide the CLI with a backend-controlled list of confirmed groups and confirmed seed members.
- Allow each suggestion to choose either a whitelisted `target_group_id` or a new `group_name`.
- Attach an existing-group suggestion as a suggested member without creating or renaming a group.
- Keep HTTPS evidence, manual confirmation, company identity validation, and SSE behavior unchanged.
- Show existing-group targets as read-only in pending review; only new-group names remain editable.

## Acceptance Gate

- Unknown, non-confirmed, or model-invented group IDs are rejected before persistence.
- Existing-group suggestions create only suggested member mappings and preserve the parent group.
- New-group suggestions continue to work as before.
- Reports remain unchanged until a browser user confirms the suggested member.
- OpenSpec strict validation, focused tests, affected regression, module gate, and browser review pass.
