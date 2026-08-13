## 1. Specification And Schema Planning

- [x] 1.1 Confirm exact DB shape for `company_groups` and `company_group_members`, including review states, uniqueness, and deletion policy.
- [x] 1.2 Define CLI/AI suggestion JSON schema and validation errors.
- [x] 1.3 Define report scope contract: parallel group reports vs explicit scope parameter.
- [x] 1.4 Define semi-automatic suggestion threshold: confirmed group seed, user target, or high-confidence internal alias/name pattern; otherwise `insufficient_evidence`.

## 2. TDD: Database And Governance

- [x] 2.1 Red: migration/schema tests for group tables, confirmed/suggested/rejected states, uniqueness, and FK/lookup behavior.
- [x] 2.2 Green: add migration and repository/service layer.
- [x] 2.3 Red: manual API tests for create/rename group, add/remove members, confirm/reject suggestions.
- [x] 2.4 Green: implement backend APIs.

## 3. TDD: CLI/AI Suggestions

- [x] 3.1 Red: suggestion schema tests reject direct confirmed writes and malformed evidence.
- [x] 3.2 Red: suggestion tests prove missing seed/target/strong pattern returns `insufficient_evidence` and no confident group.
- [x] 3.3 Green: implement suggestion ingestion as review-only data or workflow output.
- [x] 3.4 Red: CLI/AI permission tests prove suggested mappings do not affect reports until confirmed.
- [x] 3.5 Green: wire suggestion listing into governance UI/API.
- [x] 3.6 Red: manual trigger, AI job registration, web-tool allowlist, controlled-candidate, HTTPS evidence, and SSE refresh contract tests.
- [x] 3.7 Green: add the centralized `ai:company_group_suggestion` bridge runner; allow only Claude `WebSearch`/`WebFetch` and persist only validated suggestions.

## 4. TDD: Derived And Reports

- [x] 4.1 Red: two WIPS codes mapped to separate companies remain separate in company-scope reports.
- [x] 4.2 Red: after both companies are confirmed under one group, group-scope reports combine counts.
- [x] 4.3 Red: ungrouped companies fallback to their company display name in group scope.
- [x] 4.4 Green: add derived group fields and report scope/definitions.

## 5. Frontend

- [x] 5.1 Add a collapsed "group normalization" section in company governance UI.
- [x] 5.2 Support manual group creation, member selection, rename, remove, confirm suggestion, reject suggestion.
- [x] 5.3 Label report/chart scope clearly as company or group.
- [x] 5.4 Replace free-text group member identity with a selector backed by the existing company-code registry.
- [x] 5.5 Separate CLI/AI suggestions into an evidence-backed review section and keep established groups collapsed by default.
- [x] 5.6 Publish committed group mutations through SSE and refresh the visible group registry through the existing debounced resource scheduler.
- [x] 5.7 Add a manual "產生 AI 建議" trigger; disable it while the global job is queued/running and keep the suggestion section hidden when empty.

## 6. Acceptance

- [x] 6.1 Run focused unit/API tests for governance, suggestion, derived refresh, and report aggregation.
- [x] 6.2 Run OpenSpec strict validation for this change.
- [x] 6.3 Perform manual UI smoke: create group, confirm AI suggestion, refresh report, verify company/group scope difference. Supabase schema was upgraded after explicit approval; DB behavior smoke used a rollback transaction and left no test rows.
- [x] 6.4 Stop after local validation; do not push or merge until user explicitly asks.
- [x] 6.5 Re-run focused frontend/API tests, OpenSpec strict validation, and Supabase rollback behavior smoke for suggestion decisions.
- [x] 6.6 Verify SSE event contract, frontend data-event dispatch, reconnect compensation, and rendered group UI.
- [ ] 6.7 Verify the manual web-research trigger, running state, CLI execution, suggestion display, and HTTPS evidence on the deployed environment.

## 7. Product Skill

- [x] 7.1 Create a product skill for company group normalization in `skills/company-group-normalization/`.
- [x] 7.2 Remove the obsolete work-use company-name curation skill after the product skill captures the current boundary.
- [x] 7.3 Validate the product skill metadata and OpenSpec change.
