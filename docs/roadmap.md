# Roadmap

- [x] Milestone 0: workspace inspection and architecture plan
- [x] Milestone 1: runnable desktop chat
- [x] Milestone 2: bounded agent loop, validated local tools, approvals
- [x] Milestone 3: Playwright browser tools + fail-closed navigation policy
- [x] Milestone 4 (initial): connector architecture + Telegram Bot API (keyring credentials, allowlist, approval for sends)
- [x] Milestone 5: persistent autonomous task runner (SQLite queue, lifespan worker, schedules, webhooks, Automations UI)
- [x] Milestone 6: per-teammate persistent local browser computers, workspaces, preview, human takeover
- [x] Milestone 7: persistent memory, versioned skills, account/resource-scoped grants, single-agent workflows
- [x] Milestone 8: multi-agent teams, bounded coordinator, dependency-aware subtasks
- [x] Milestone 9: always-on service mode, device pairing, companion UI, diagnostics
- [x] Milestone 10: ComputerProvider abstraction, mock + isolated_linux_dev, lifecycle/takeover/health/delete, billable stub (no paid VMs without approval)
- [x] M10+ GCP path: `gcp_compute` + credit-funded gate + Fake client tests (live GCE VM pending owner credentials)
- [x] Migration slice: durable job contract (`JobEnvelope` / `JobDispatcher`) + `cloud_sandbox` registration
- [x] Daytona adapter behind `cloud_sandbox` (Fake client tests; real SDK when key + enable + confirm)
- [x] Video extras: optional Clerk JWKS auth (off by default)
- [x] Video extras: optional Neon/Postgres via `DATABASE_URL` + `/ready` dialect note
- [x] Video extras: `trigger_http` durable enqueue webhook (off by default; in_process remains default)
- [x] Video extras: Gmail/GitHub OAuth code exchange + read tools (needs your OAuth apps + enable flag)
- [x] Milestone 4 (later): GitHub/Gmail OAuth authorize/callback + keyring tokens + read-only tools
- [x] Live verify: one Daytona sandbox provision + exec + stop + delete (`scripts/live_daytona_smoke.py` → SMOKE_OK)
- [ ] Live verify: credit-funded GCE VM (needs owner re-enable + confirm)
- [x] Daytona computer_use + desktop tools + UI provision (prior slices)
- [x] Point `trigger_http` at Trigger.dev REST API (`ai-teammates-enqueue` task); default runner still `in_process`
- [x] Clerk JWKS/issuer wired; React `@clerk/react` optional via `VITE_CLERK_PUBLISHABLE_KEY` (keep `CLERK_ENABLED=false` until companion auth needed)
- [ ] Future (separate approval): out-of-pocket / PAYG billable cloud (explicitly not authorized now)

### Daytona / durable — verified vs stubbed

| Item | Status |
| --- | --- |
| `local_browser` default path | Verified by tests |
| `JobDispatcher` → `in_process` AutomationWorker | Verified |
| `external_stub` durable backend | Stub — refuses until implemented |
| `cloud_sandbox` + FakeDaytonaClient provision/idempotent/delete | Verified by tests |
| Real Daytona live sandbox | Verified (create/exec/stop/delete smoke) |
| In-sandbox computer_use + PNG preview | Verified (Fake + live smoke path) |
| Playwright `browser_*` on Daytona | Not ported — use `desktop_*` instead |
| `desktop_open_url` + nav policy | Verified (blocks localhost; opens https via Fake exec) |
| AgentRuntime cloud_sandbox hints | Verified |
| `desktop_*` tools on cloud_sandbox | Verified (Fake + PermissionBroker); live click/type in smoke |
| UI provision Daytona + API estimate/confirm | Verified (API Fake test + UI wired) |
| Clerk auth | JWKS/issuer configured; UI optional; `CLERK_ENABLED` still off by default |
| Neon/Postgres | Neon URL supported; `/ready` reports dialect |
| `trigger_http` durable enqueue | Trigger.dev API mode + `ai-teammates-enqueue` task; refuse-without-URL tested |
| Gmail/GitHub OAuth | Authorize/callback + keyring + read tools (enable + OAuth apps required) |
| Billable / PAYG | Still requires explicit owner approval (do not enable casually) |
