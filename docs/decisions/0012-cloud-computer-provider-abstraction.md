# ADR 0012 — Cloud computer provider abstraction (M10)

## Status

Accepted (Milestone 10) — development providers verified; billable cloud **not** provisioned

## Context

M6 gave each teammate a persistent local Playwright profile + workspace. M9 made the control plane optionally always-on on the host. Users want a genuine isolated Linux computer that can outlive a laptop — without pretending a browser context is a VM, and without silently opening a paid cloud account.

## Decision

1. **`ComputerProvider` protocol** (`backend/app/computers/base.py`) defines lifecycle: provision, start, stop, health, browser open/close, status, preview, human control, delete, workspace paths.
2. **Two real execution modes today:**
   - `local_browser` — existing M6 Windows/local Playwright computer (default).
   - `isolated_linux_dev` — filesystem + browser-profile sandbox with isolation manifest; **not** a hypervisor VM.
3. **`mock_cloud`** — contract tests only; never presented as production cloud.
4. **`billable_cloud` stub** — refuses `provision()` until `AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE=true`, credentials exist outside chat, and the owner explicitly approves paid resources.
5. **Stable computer ID per teammate**; ownership enforced server-side (`assert_owner`). Provider change requires delete + confirm — no silent migration of local → cloud.
6. **Control plane vs execution environment:** FastAPI + AutomationWorker remain the orchestrator. Laptop-independent runs require deploying that control plane to an always-on host (M9), not only a remote desktop.
7. **Recommended production path (not implemented/billed):** a managed isolated Linux desktop (e.g. Fly Machines / cloud VM with XFCE + authenticated gateway) behind the same protocol — chosen only after API review and owner approval.

## Lifecycle vocabulary

| Action | Effect on runtime | Effect on workspace |
|--------|-------------------|---------------------|
| Pause | Agent actions blocked | Kept |
| Stop | Browser/process stopped | Kept |
| Restart/start | Process recreated | Kept (same root) |
| Reset/clear browser | Profile wiped | Workspace kept |
| Delete (confirm) | Record + root removed | **Destroyed** |

## Consequences

- Automated tests never create billable VMs.
- `isolated_linux_dev` is honest about isolation limits (host filesystem sandbox ≠ VM).
- Secrets and cloud control-plane credentials must not be mounted into computer roots.
- Shell/terminal tools stay disabled by default.

## Approval gate for billable cloud

Before any paid provision: owner chooses provider, supplies credentials via env/secret store (not chat), sets `COMPUTER_ALLOW_BILLABLE`, and confirms cost limits.
