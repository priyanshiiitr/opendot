# AI Teammates

A local-first desktop AI teammate. Milestone 1 delivers desktop chat with streaming and persistence. Milestone 2 adds a bounded, backend-controlled agent loop with deterministic local tools, approvals, execution events, and durable traces.

## Prerequisites

- Windows 10/11
- Node.js 20+ and npm
- Python 3.11+ (this workspace uses Python 3.14 with a project-local `.venv`)

## Setup

```powershell
npm install
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

Keep `AI_TEAMMATES_MODEL_PROVIDER=mock` for offline development. To use xAI, set `AI_TEAMMATES_MODEL_PROVIDER=xai` and put the API key in `backend/.env`. The key is read only by Python and never returned to the renderer.

## Run

Terminal 1 — backend:

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2 — desktop UI:

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dev
```

Health check: `http://127.0.0.1:8000/health`.

If Electron was installed with `npm install --ignore-scripts`, repair it with a project-local cache:

```powershell
$env:npm_config_cache = "$PWD\.npm-cache"
$env:electron_config_cache = "$PWD\.electron-cache"
npm rebuild electron
```

## Verify

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
cd ..
npm test
npm run build
```

Or, with the venv activated: `cd backend` then `python -m pytest`. `pytest.ini` adds the backend package root to `pythonpath`.

## Milestone 2: safe local agent loop

In mock mode, try:

- `add 12 plus 30` — structured `calculate` tool call, real arithmetic, final answer uses the tool result
- `what time is it?` — `get_current_time` with timezone `Asia/Calcutta` by default
- `request approval` — `demo_request_approval` pauses for Approve / Reject; rejection never executes

The backend validates JSON arguments with Pydantic, enforces iteration and tool-call limits, times out tool handlers, and fails closed for unknown tools. Mock mode is offline. xAI tool-call streaming is unit-tested with mocked SSE chunks; live xAI verification still needs a compatible account and model access.

## What this app intentionally does not do yet

No computer control, shell execution, browser automation, Telegram/Gmail/GitHub connectors, or multi-agent delegation. Those are later milestones.

## Environment note

If Electron starts as Node and `app` is undefined, remove `ELECTRON_RUN_AS_NODE` for that launch only:

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dev
```
