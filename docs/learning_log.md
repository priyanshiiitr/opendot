# Learning log

## Milestone 1–2 (summary)

- Renderer never sees provider credentials; SSE streams tokens/events.
- Structured tool calls ≠ ordinary text. Backend validates, authorizes, executes, persists.
- Approval is a backend state machine. Windows needs `tzdata` for `zoneinfo`.

## Milestone 3

- **Playwright process vs context:** the process is Chromium; the context is the isolated cookie jar. Never reuse the everyday Chrome profile.
- **String hostname checks are not enough:** `http://evil.example` can resolve to `10.0.0.1`. Resolve DNS, classify IPs with `ipaddress`, then navigate; recheck after redirects.
- **Fixture exception must be explicit:** tests bind a local `ThreadingHTTPServer` on `127.0.0.1:ephemeral` and enable fixture mode only for that host:port.
- **Prompt injection via webpages:** treat snapshot/extract text as evidence. Permissions stay in `PermissionBroker` code paths the page cannot call.
- **Why browser before computer-use:** selectors, fixtures, and network policy are testable; raw desktop control is wider and harder to prove safe in an interview.

## Interview trace (fixture research)

User: “research startups” → Mock emits `browser_open` → `NavigationPolicy` allows fixture URL → Playwright opens page → Mock emits `browser_extract` → companies returned → final answer lists NovaMind/OrbitAgents/PixelForge from the tool result (not invented).

## Milestone 4

- **Connector ≠ agent runtime:** connectors only register tools; `AgentRuntime` / `PermissionBroker` stay in charge.
- **Bot API vs user account:** a BotFather token lets the bot act as itself; it is not Telethon/user user-session access to your personal chats.
- **Secrets:** OS keyring for tokens; SQLite stores allowlists and bot username only. Status APIs never return the token.
- **Allowlists:** unread chats from strangers never become automatic instructions; message text is untrusted evidence.
- **Sends need approval** with the exact `chat_id` + text frozen in the approval payload; duplicates cannot re-fire.

## Milestone 5

- **Definition vs run:** an Automation is the plan; an AutomationRun is one receipt. Interviewers love this split.
- **Why SQLite queue, not Celery:** one user, one machine — Redis would be ceremony without benefit.
- **Lease claim:** `UPDATE … WHERE status='queued'` is the concurrency primitive; two workers cannot own the same row.
- **Clean context:** each run gets a fresh conversation so yesterday’s chat does not silently expand tools or tokens.
- **Crash rule:** if a consequential tool was `running`, mark `needs_reconciliation` — never guess that Telegram did or did not send.
- **Approvals survive restart:** `waiting_approval` is never auto-approved on boot.
- **Schedules:** timezone via `zoneinfo`; `occurrence_key` prevents duplicate fires; missed policy skips catch-up floods.
- **Webhooks:** separate route, hashed secret, dedupe by event id, enqueue only — do not run the agent inside the HTTP request.
