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
- [ ] Milestone 4 (later): additional connectors (Gmail/GitHub/etc.) without rewriting the agent runtime
- [ ] Live verify: provision one credit-funded GCE VM + guest-agent preview after owner configures GCP trial/ADC
- [ ] Future (separate approval): out-of-pocket / PAYG billable cloud (explicitly not authorized now)
