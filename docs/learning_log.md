# Learning log

## Milestone 1

- The renderer should not receive provider credentials; it only calls the local API.
- Server-Sent Events (SSE) stream small events over one HTTP response, which makes token-by-token UI updates straightforward.
- Conversation history and assistant output are persisted in SQLite; the assistant row is written only after a complete provider stream.
- SQLAlchemy keeps persistence models explicit and allows a later database migration without rewriting route code.
- Python 3.14 works with the current FastAPI / SQLAlchemy / Uvicorn / pytest set in a project-local `.venv`.
- Electron's postinstall can fail with `EPERM` under `%LOCALAPPDATA%`; pointing `electron_config_cache` at `.electron-cache` repairs it without admin rights.
- `ELECTRON_RUN_AS_NODE=1` forces Electron to behave like Node (`app` undefined). Remove it for the desktop launch process only.
- The full mock flow was previously verified in the real desktop UI: send, stream, restart, persistence, and backend-unavailable recovery.

## Milestone 2

- Model text has no authority; only typed provider `tool_calls` reach the registry.
- Tool-call argument fragments may arrive over SSE. The xAI adapter accumulates by index and parses JSON only when complete.
- Every new model turn rebuilds history from persisted messages, so the provider receives the actual tool result rather than a claimed one.
- Approval is a backend state machine (`waiting_approval` → `approved` / `rejected`), not a sentence in a prompt.
- The frontend must require an explicit terminal event (`done` or `approval_required`). Treating TCP close as success hides cancellations and crashed streams.
- Windows Python often lacks a system tz database; installing `tzdata` makes `zoneinfo` / IANA names such as `Asia/Calcutta` work.
- Prefer a tiny explicit agent loop while learning: you can point to the exact validation, permission, timeout, and persistence lines in an interview.

## How to explain one request in an interview

User types “add 12 plus 30” → React POSTs JSON → FastAPI stores the user message → `MockProvider` emits a structured `calculate` call → `ToolRegistry` validates numbers → `PermissionBroker` allows read-only execution → `calculate` returns `{result: 42.0}` → result is persisted → provider is called again with a `tool` message → final streamed tokens say the result is 42 → UI shows activity cards and the assistant bubble.
