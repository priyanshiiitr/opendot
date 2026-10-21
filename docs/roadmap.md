# Roadmap

- [x] Milestone 0: workspace inspection and architecture plan
- [x] Milestone 1: runnable desktop chat
- [x] Milestone 2: bounded agent loop, validated local tools, approvals, execution events
- [x] Milestone 3 (initial slice): Playwright browser tools + fail-closed navigation policy + isolated context + fixture tests
- [ ] Milestone 3 (hardening): richer UI for screenshots, host allowlists for production browsing, live research demos
- [ ] Milestone 4: one connector with OAuth and audit records
- [ ] Milestone 5: configurable teammates and bounded delegation
- [ ] Milestone 6: controlled desktop operations and resumable work

## Milestone 3 security gates

1. [x] Only `http`/`https`; unsupported schemes fail closed
2. [x] Localhost/private/link-local/metadata blocked after DNS/IP checks
3. [x] Redirect targets rechecked (fixture redirect-to-private test)
4. [x] Isolated Playwright context (no user Chrome profile)
5. [x] Page content cannot change PermissionBroker; prompt-injection fixture refused by mock final answer
6. [x] `browser_click` / `browser_type` require approval
7. [x] Fixture mode is explicit and port-scoped; default app mode keeps it off
8. [x] Tests use local fixtures/mocks only — no probing of real internal networks; no paid API calls
