# Architecture

```text
User -> React/Electron -> FastAPI
                            |-> AgentRuntime (chat SSE or automation worker)
                            |      |-> MockProvider / GroqProvider / XAIProvider
                            |      |-> ToolRegistry + PermissionBroker
                            |-> AutomationWorker (lifespan) -> claim AutomationRun -> AgentRuntime
                            |-> Scheduler tick -> enqueue due automations (occurrence_key)
                            |-> /hooks/{id} webhook receiver (bearer, hashed secret)
                            |-> BrowserService / ConnectorManager / SQLite
```

See ADR `0007-durable-automations-sqlite-worker.md`.

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

## SSE event types

| Event | Meaning |
| --- | --- |
| `run_started` | New `AgentRun` id |
| `token` | Assistant text fragment |
| `execution` | Persisted activity (`queued`, `validated`, `running`, `completed`, `failed`, `rejected`, `waiting_approval`) |
| `approval_required` | Pause for user decision |
| `done` | Turn completed successfully |
| `error` | Controlled failure |
