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
