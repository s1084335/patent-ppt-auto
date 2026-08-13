# Change: Edit pending company group suggestion name

## Why

CLI/AI may suggest a legally descriptive but overly long group name. The browser currently renders
that name as fixed text, so a reviewer must confirm it first and rename the established group in a
second operation.

## What Changes

- Render the pending suggestion group name as an editable field shared by that group's members.
- Submit the edited name with a confirm action and persist the rename in the same transaction.
- Keep reject name-neutral and preserve clients that confirm without a request body.
- Keep the 255-character database/API boundary and the existing `companyGroups` SSE refresh.

## Acceptance Gate

- A reviewer can shorten or replace the pending group name before confirmation.
- Confirm atomically persists the trimmed name and confirms only the selected member.
- Blank and over-255-character names are rejected; an untouched name remains unchanged.
- Reject does not rename the group, and body-less confirm remains backward compatible.
- OpenSpec strict validation, focused API/repository/frontend tests, affected regression, and browser
  interaction/visual inspection pass.
