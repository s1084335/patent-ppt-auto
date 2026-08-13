## 1. Controlled existing-group suggestions

- [x] 1.1 Record scope, write boundary, and acceptance gate.
- [x] 1.2 Red: cover controlled group input, target validation, persistence, and read-only review UI.
- [x] 1.3 Green: load confirmed group seeds and validate `target_group_id` in the runner.
- [x] 1.4 Green: persist suggested members under existing confirmed groups without creating parents.
- [x] 1.5 Green: distinguish existing-target and new-group suggestions in browser review.
- [x] 1.6 Update the product skill and run combined acceptance.

## Acceptance Evidence (2026-08-13)

- Red: `5 failed, 42 passed`; missing controlled group prompt, target validation, existing-parent
  persistence, and read-only review behavior were observed before Green.
- Focused Green/Refactor: `55 passed`; affected combined regression: `118 passed, 16 subtests passed`.
- `scripts/verify_module.py`: new-line lint 0, all 16 touched functions CC A-B, new-line coverage
  `94/103 = 91%`.
- OpenSpec strict validation passed. Playwright desktop 1440x900 and mobile 390x844 passed with
  zero control overlap and zero page errors; existing-target confirmation sent no `group_name`,
  while new-group confirmation sent the edited name.
- SSE wiring: ingest and review transactions emitted `data/companyGroups` payloads; two browser
  data events inside the debounce window caused one registry refresh, with each authoritative API
  requested exactly once.
- Not run by product decision/environment boundary: real CLI web research, Supabase write smoke,
  Lightning deployment, and Companion restart.
