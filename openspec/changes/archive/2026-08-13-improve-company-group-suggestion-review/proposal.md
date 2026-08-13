# Change: Improve company group suggestion review

## Why

The browser currently renders `evidence_json` as one raw JSON string. Reviewers cannot quickly
identify confidence, evidence sources, supporting claims, or warnings before confirming a mapping.

## What Changes

- Render each pending company-group suggestion as a structured review item.
- Show localized confidence, compact `來源` links, supporting claims, and separate warnings.
- Allow a confirmed AI suggestion to return to pending review without losing its evidence.
- Keep single-member removal and allow users to dissolve an entire group mapping.
- Keep the existing suggestion data, review actions, API, and SSE refresh behavior unchanged.

## Acceptance Gate

- Raw evidence JSON is not shown in the browser.
- Company name/code, confidence, `來源` links, claims, and warnings are visually distinguishable.
- External links accept HTTPS URLs only and all AI-provided text remains HTML escaped.
- Missing optional evidence fields render a useful fallback without breaking review actions.
- Undoing an AI confirmation preserves evidence and returns the member to pending review.
- Removing one member remains available; dissolving a group removes only group mappings.
- Every reversal publishes the existing `companyGroups` SSE refresh event.
- OpenSpec strict validation, the focused frontend contract test, and visual browser inspection pass.
