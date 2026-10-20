# Learning log

## Milestone 1

- The renderer should not receive provider credentials; it only calls the local API.
- Server-Sent Events (SSE) stream small events over one HTTP response, which makes token-by-token UI updates straightforward.
- Conversation history and assistant output are persisted in SQLite; the assistant row is written only after a complete provider stream.
- SQLAlchemy keeps persistence models explicit and allows a later database migration without rewriting route code.
- Python 3.14 was installed after the initial implementation. A project-local `.venv` installs and runs the current FastAPI, SQLAlchemy, Uvicorn, and pytest dependency set successfully.
- Electron's postinstall initially failed because npm's default Electron cache under `%LOCALAPPDATA%` rejected directory creation with `EPERM`. Rebuilding with `electron_config_cache` pointed at `.electron-cache` repaired the installation without administrator privileges.
- `ELECTRON_RUN_AS_NODE=1` was also present in the session environment. It forced Electron to execute the main file as Node, making `app` undefined. Removing that variable for the child launch produced the actual desktop window.
- The full mock flow was verified in the real desktop UI: send, streamed mock response, backend restart, application restart, persistence, and an explicit backend-unavailable error state.

## Milestone 2

- Model text has no authority; only typed provider tool calls reach the registry.
- Tool-call fragments may arrive over SSE. The xAI adapter accumulates them and parses JSON only when complete.
- Every new model turn rebuilds history from persisted messages, so it receives the actual tool result rather than an ORM-cached snapshot.
- The approval demo is local and side-effect free; it proves that approval is a backend state transition, not a model claim.
