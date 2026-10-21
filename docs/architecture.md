# Architecture

```text
User -> React/Electron -> FastAPI -> AgentRuntime -> ModelProvider
                                              |          |-> MockProvider
                                              |          |-> XAIProvider
                                              |-> ToolRegistry -> calculate / get_current_time / demo_request_approval
                                              |-> PermissionBroker -> allow | approval_required | deny
                                              |-> SQLite (messages, agent_runs, execution_events)
```

## Request path for a tool-using turn

1. The renderer POSTs the user message to `/api/conversations/{id}/messages`.
2. FastAPI persists the user message and starts an `AgentRun`.
3. `AgentRuntime` rebuilds provider history from SQLite (user, assistant, assistant tool-call records, tool results).
4. The selected `ModelProvider.stream_turn(...)` either streams text tokens or emits structured `ToolCall` objects. Ordinary assistant text never becomes executable.
5. For each structured call, the registry validates JSON size and Pydantic schemas, then the permission broker decides allow / pause / deny.
6. Allowed tools execute under `asyncio.wait_for` with size limits. Results are stored as `role=tool` messages and `ExecutionEvent` rows.
7. Approval-required tools create a waiting approval event and pause the run. Approve / Reject hits `/api/conversations/{id}/approvals/{approval_id}` and is a backend state transition.
8. After tool results exist, the runtime calls the provider again until it produces final text, hits a configured limit, errors, cancels, or waits for approval.
9. The UI consumes typed SSE events (`token`, `execution`, `approval_required`, `done`, `error`). Stream close without `done` or `approval_required` is treated as failure.

## Why this shape

- **Backend-owned loop:** The model proposes; the server authorizes and executes. That boundary is interview-critical for agent safety.
- **Small explicit loop over an agent framework:** Easier to inspect, test, and explain. Frameworks can wrap this later if needed.
- **Provider-neutral events:** `ProviderEvent` / `ToolCall` keep Mock and xAI behind one interface.
- **SQLite traces:** `AgentRun` + `ExecutionEvent` reconstruct tool history after restart without inventing a second database.

## Security and concurrency notes

- Unknown tools and unclassified permissions fail closed.
- The model cannot register tools, mutate permissions, or self-approve.
- Tool argument and result byte limits reduce prompt-injection / data-exfiltration surface from tool payloads.
- Cancellation marks the run; a cancelled waiting approval cannot later execute.
- Tool handlers are async and timeout-bounded so they do not permanently block the event loop.
- On Windows, IANA timezones require the `tzdata` package because the OS zone database is not always available to `zoneinfo`.

## SSE event types

| Event | Meaning |
| --- | --- |
| `run_started` | New `AgentRun` id |
| `token` | Assistant text fragment |
| `execution` | Persisted activity (`queued`, `validated`, `running`, `completed`, `failed`, `rejected`, `waiting_approval`) |
| `approval_required` | Pause for user decision; UI shows Approve / Reject |
| `done` | Turn completed successfully |
| `error` | Controlled failure (limits, provider, invalid approval, cancel) |
