# AI Teammates

A local-first desktop AI teammate. Milestone 2 adds a bounded, backend-controlled agent loop with deterministic local tools, approvals, event streaming, and durable execution traces.

## Prerequisites

- Windows 10/11
- Node.js 20+ and npm
- Python 3.11+ (the current workspace does not have Python installed)

## Setup

```powershell
npm install
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

If `py --version` reports `No installed Python found`, install Python 3.11+ from the official python.org Windows installer with “Add python.exe to PATH” enabled. This environment has no Python installation and no `winget`, so the installer step could not be performed here without user action.

Keep `AI_TEAMMATES_MODEL_PROVIDER=mock` for offline development. To use xAI, set `AI_TEAMMATES_MODEL_PROVIDER=xai` and put the API key in `backend/.env`; it is read only by Python and never returned to the renderer.

## Run

In one terminal:

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal from the project root:

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dev
```

The Electron window opens after Vite starts. The backend health endpoint is `http://127.0.0.1:8000/health`.

If Electron was installed with `npm install --ignore-scripts`, repair it with a project-local cache and no administrator privileges:

```powershell
$env:npm_config_cache = "$PWD\.npm-cache"
$env:electron_config_cache = "$PWD\.electron-cache"
npm rebuild electron
```

## Verify

```powershell
cd backend
python -m pytest
cd ..
npm test
npm run build
```

The application intentionally does not implement computer control, shell execution, browser automation, connectors, or multi-agent delegation. Those belong to later milestones.

## Milestone 2: safe local agent loop

In mock mode, try `add 12 plus 30`, `what time is it?`, or `request approval`. The first two execute registered local tools; the last shows a harmless simulated approval card. The backend validates JSON arguments, enforces four iterations and six calls per turn, and fails closed for unknown tools. Mock mode is offline; xAI tool calling is unit-tested with mocked SSE chunks but needs a real compatible xAI account for live verification.

## Verification status

- Frontend unit test: passed (`npm test`).
- Production frontend build: passed (`npm run build`).
- Electron package repair: passed; `node_modules\electron\dist\electron.exe` exists and reports a version.
- Backend tests: passed (`python -m pytest`): 2 tests passed.
- Backend health endpoint, SQLite persistence, mock-provider SSE stream, and restart persistence: verified against a running loopback Uvicorn server.
- Desktop launch: verified in an actual Electron window. A message sent through the composer streamed a mock response, and the conversation remained after the app restart.
- Backend-unavailable UI: verified. Reloading the desktop application without the local backend displayed `Failed to fetch`; restoring the backend and reloading recovered the conversation.

### Environment note

If Electron starts as Node and `app` is undefined, remove `ELECTRON_RUN_AS_NODE` for that launch only:

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dev
```
