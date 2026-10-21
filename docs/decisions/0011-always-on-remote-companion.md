# ADR 0011 — Always-on service + optional remote companion

## Status

Accepted (Milestone 9)

## Context

M5–M8 already run durable work inside the FastAPI process. Users need the agent service to outlive the Electron window, and optionally supervise from a phone — without exposing an unrestricted public API.

## Decision

1. **Service ≠ UI:** Document and script `scripts/start-backend.ps1` so uvicorn + AutomationWorker run without Electron. Optional user-level Startup shortcut only when the owner runs `register-user-startup.ps1`.
2. **Default bind `127.0.0.1`; remote access off.** `AI_TEAMMATES_REMOTE_ACCESS_ENABLED` must be set explicitly.
3. **Loopback trust:** When remote is on, requests from `127.0.0.1` still work without a token so the desktop remains usable (`local_trust_loopback`).
4. **Device pairing:** Short-lived code (hashed at rest) started only from loopback; companion exchanges code for a one-time session token (hashed). Revocation and expiry fail closed.
5. **Auth middleware:** Non-loopback requests require a valid session when remote access is enabled. Public: `/health`, `/ready`, `/api/remote/status`, pairing complete, webhooks.
6. **Companion UI:** Responsive React views (Companion + Ops) reuse existing APIs; session token in `sessionStorage` + header — not query strings.
7. **Notifications:** Reuse `AppNotification`; bodies stay short/safe. Web Push deferred (needs secure context / TLS).

## Alternatives considered

- Always-on Windows Service as SYSTEM — rejected (privilege + silent install risk).
- Public Cloudflare tunnel by default — rejected; private VPN is the documented path.
- Permanent shared password in the frontend — rejected.

## Consequences

- Jobs do not run when the PC is off.
- Enabling remote without changing `bind_host` still cannot reach other devices.
- Pairing grants application API access only — not raw filesystem/secret store/shell.
