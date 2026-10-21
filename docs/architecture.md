# Architecture

```text
User -> React/Electron -> FastAPI
                            |-> AgentRuntime (binds current_teammate_id ContextVar)
                            |      |-> MemoryService (scoped retrieve → system prompt)
                            |      |-> MockProvider / GroqProvider / XAIProvider
                            |      |-> ToolRegistry + PermissionBroker(+ AuthContext)
                            |-> ComputerManager -> ComputerProvider implementations:
                            |      local_browser | isolated_linux_dev | (billable_cloud stub)
                            |-> SkillRegistry (local SKILL.md packages; review before enable)
                            |-> WorkflowService (steps, checkpoints, artifact verify)
                            |-> TeamCoordinator -> TeamRun/Subtask -> AgentRuntime (per assignee)
                            |-> AutomationWorker (lifespan) -> claim AutomationRun -> AgentRuntime
                            |-> RemoteAuthMiddleware (optional; off by default)
                            |-> /hooks/{id} webhook receiver
                            |-> ConnectorManager + ConnectorGrant / SQLite
```

Clients: Electron desktop, Vite web, companion view — all talk to the same FastAPI service. See ADR `0007`–`0012`.

## Request path for a tool-using turn

1. The renderer POSTs the user message to `/api/conversations/{id}/messages`.
2. FastAPI persists the user message and starts an `AgentRun`.
3. `AgentRuntime` rebuilds provider history from SQLite.
4. The provider streams text or structured `ToolCall` objects. Ordinary assistant text never executes.
5. The registry validates arguments; the permission broker allows, pauses, or denies.
6. Browser tools call `BrowserService`, which runs every navigation through `NavigationPolicy` before and after redirects.
7. Results persist as tool messages and `ExecutionEvent` rows; the UI shows activity cards and approval controls.

## Browser security (Milestone 3)

See `docs/decisions/0004-browser-navigation-security.md` and `0005-playwright-isolated-context.md`.

- **Process vs context:** Playwright launches a Chromium **process**; each session uses a fresh **context** with no imported cookies/passwords from the user’s Chrome profile.
- **Destination policy:** only `http`/`https`; DNS-resolved IPs must be public; localhost/private/link-local/metadata blocked; redirects rechecked; fail closed.
- **Fixture exception:** `AI_TEAMMATES_BROWSER_FIXTURE_MODE` + host/port — test-only, never default app mode.
- **Untrusted pages:** snapshots/extracts include an explicit note; page text cannot approve tools or change policy. `browser_click` / `browser_type` stay `approval_required`.

## Why this shape

- **Backend-owned loop:** The model proposes; the server authorizes and executes.
- **Small explicit loop over an agent framework:** Easier to inspect and teach.
- **Playwright before computer-use:** Browser automation is easier to sandbox, test with fixtures, and explain in interviews than unrestricted OS mouse/keyboard control.

## Security and concurrency notes

- Unknown tools and unclassified permissions fail closed.
- The model cannot register tools, mutate permissions, or self-approve.
- Tool argument/result byte limits apply; browser extracts have a separate size cap.
- Cancellation marks the run; cancelled approvals cannot execute.
- Browser actions are serialized with an asyncio lock and capped per session.
- On Windows, `tzdata` is required for IANA timezones.
- Telegram tokens live in OS keyring only; connector SQLite rows hold allowlists/metadata. Sends require approval with exact chat/text binding. Incoming Telegram text is untrusted evidence (ADR 0006).

## Automations (Milestone 5)

| Concept | Role |
| --- | --- |
| `Automation` | Definition: instruction, schedule, timezone, limits, autonomy policy |
| `AutomationRun` | One durable execution: `queued` → `running` → terminal / `waiting_approval` |
| Worker | Claims with `UPDATE … WHERE status='queued'`; not an untracked task |
| Checkpoint | Last safe step; consequential tools may force `needs_reconciliation` |
| Occurrence key | Unique scheduled fire identity — ticks/restarts cannot double-enqueue |
| Webhook | `/hooks/{id}` separate from `/api`; enqueue only, never full sync agent loop |

**Local-only truth:** schedules and webhooks process only while the backend is running. Missed runs follow `skip_catch_up` by default (no flood after multi-day offline).

## Memory, skills, grants, workflows (Milestone 7)

| Subsystem | Role | Does **not** |
| --- | --- | --- |
| Conversation history | What was said in one chat | Act as long-term preference store |
| `MemoryService` | Scoped prefs/facts with provenance + soft-delete | Store secrets; auto-save every reply |
| Skills | Versioned instruction packages + evaluation criteria | Grant tools, run scripts, install deps |
| `ConnectorGrant` + `AuthContext` | Per-teammate connector ops when present | Let the model pick another user’s account |
| `WorkflowService` | Step graph, checkpoints, deterministic verify | Multi-agent delegation; claim success from prose |

Data flow for “use my startup-research skill”: retrieve memories → resolve enabled skill install → create workflow with pinned `skill_version` + `memory_ids` → advance safe steps → write workspace artifact → verify → report. Backend authorization remains authoritative at every tool call.

## Multi-agent teams (Milestone 8)

| Piece | Role |
| --- | --- |
| `Team` | Configurable roster + limits (not a fixed five-agent cast) |
| `validate_plan` | Rejects cycles, unknown deps, unauthorized assignees, depth/count breaches |
| `TeamCoordinator.tick` | Dependency-aware scheduling; each subtask = fresh conversation + AgentRuntime |
| Structured result | findings, artifacts, limitations — not “fluent text = success” |
| Final report | Distinguishes completed / failed / unverified; critical failures ⇒ run failed |

Least privilege: subtask `allowed_tool_categories` restrict the broker; shared runtime lock serializes tool turns. `max_depth=1` blocks specialist re-delegation by default.

## Always-on & remote companion (Milestone 9)

| Concern | Behavior |
| --- | --- |
| Service lifecycle | `uvicorn` + lifespan worker; scripts under `scripts/` |
| Default exposure | `127.0.0.1`, remote pairing disabled |
| Pairing | Loopback-only start → one-time code → hashed session token |
| Auth | Non-loopback requires session when remote enabled; loopback trusted for desktop |
| Companion | Responsive UI: runs, approvals, notifications; disconnect ≠ cancel |
| Honest limits | No work when PC off; DB “ready” ≠ live browser process |

## Computer providers (Milestone 10)

| Layer | Responsibility |
| --- | --- |
| Control plane | FastAPI, PermissionBroker, AutomationWorker, ComputerManager — never called directly by the model’s cloud SDK |
| Execution environment | Per-teammate computer root (profile + workspace + artifacts) |
| `ComputerProvider` | Protocol: provision/start/stop/health/preview/takeover/delete |
| `local_browser` | M6 Playwright persistent context on the host |
| `isolated_linux_dev` | Same host, stronger filesystem boundary + isolation.json; not a VM |
| `mock_cloud` | Contract tests only |
| `billable_cloud` | Stub; fails closed until allow flag + owner approval |

**Laptop independence:** a cloud desktop without a remote control plane still dies when the orchestrator laptop is off. Deploy M9’s always-on service remotely to continue durable tasks; do not auto-migrate SQLite without an approved plan.

**Takeover states:** `agent_control` → `pause_requested` → `waiting_for_safe_point` → `human_control` → `returning_to_agent` → `agent_control`. Backend owns exclusivity via action locks.

**Secrets:** model API keys stay on the control plane. Computer roots must not receive host `.env`, Docker sockets, or cloud control-plane credentials.

## SSE event types

| Event | Meaning |
| --- | --- |
| `run_started` | New `AgentRun` id |
| `token` | Assistant text fragment |
| `execution` | Persisted activity (`queued`, `validated`, `running`, `completed`, `failed`, `rejected`, `waiting_approval`) |
| `approval_required` | Pause for user decision |
| `done` | Turn completed successfully |
| `error` | Controlled failure |
