# ADR 0008 — Per-teammate local browser computers

## Status

Accepted (Milestone 6)

## Context

M3 provided one shared ephemeral Playwright context. Users need each teammate to keep its own cookies/workspace across restarts, preview the page, and take over the same session without sharing another teammate’s profile.

## Decision

1. **`ComputerProvider` / `LocalBrowserComputerProvider`** — abstraction for a future VM/cloud desktop; only the local Playwright provider is implemented.
2. **`ComputerManager`** — one stable `ComputerRecord` per `teammate_id`; roots under `%LOCALAPPDATA%/AITeammates/computers/<id>/`.
3. **Persistent profile** via `launch_persistent_context` + exclusive profile lock file. Never import everyday Chrome.
4. **ContextVar binding** — `AgentRuntime` sets `current_teammate_id`; tools resolve the computer from that, never from model-supplied IDs.
5. **Human takeover** — backend state `human_control` + `agent_blocked_reason`; reopen headed on the same profile when needed; agent browser mutations blocked until release; human_control cleared on restart reconcile.
6. **Workspace tools** — path-canonicalized reads/writes under that computer’s workspace/artifacts only.
7. **Preview** — bounded PNG endpoint with client refresh; not a remote stream.

## Alternatives

| Option | Rejected because |
| --- | --- |
| Shared browser for all teammates | Cross-session credential leak |
| Full Linux VM now | Out of scope; provider interface leaves room later |
| Model-chosen computer IDs | Trivial cross-teammate escape |
| Auto-resume human_control after crash | Stale lock / unsafe ownership |

## Consequences

- Local browser ≠ VM isolation.
- Auth sessions may expire; clearing profile is explicit and confirmed.
- Fixture tests may still inject an ephemeral `BrowserService` without persistence.
