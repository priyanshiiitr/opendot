# AI Teammates

A local-first desktop AI teammate. Milestone 2 adds a bounded agent loop with local tools and approvals. Milestone 3 adds **Playwright browser tools** with fail-closed navigation policy and isolated browser contexts.

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
python -m playwright install chromium
Copy-Item backend\.env.example backend\.env
```

Keep `AI_TEAMMATES_MODEL_PROVIDER=mock` for offline development. Keep `AI_TEAMMATES_BROWSER_FIXTURE_MODE=false` in normal use — fixture mode is **test-only**.

To use xAI, set `AI_TEAMMATES_MODEL_PROVIDER=xai` and put the API key in `backend/.env`. The key is read only by Python and never returned to the renderer.

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

## Verify

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
cd ..
npm test
npm run build
```

## Milestone 2 prompts (still work)

- `add 12 plus 30`
- `what time is it?`
- `request approval`

## Milestone 3: browser teammate (fixture / mock)

Browser tools are registered in the same `ToolRegistry`. Navigation allows only `http`/`https`, validates DNS/IPs, blocks private/loopback/metadata destinations, and rechecks after redirects. Playwright uses a fresh isolated **browser context** (not your everyday Chrome profile).

Automated tests serve HTML under `backend/tests/fixtures/browser/` with an explicit fixture-mode exception. In normal app mode that exception is off, so local/private destinations fail closed.

Mock scenarios (require fixture server + fixture settings used by tests):

- `research startups` → `browser_open` → `browser_extract` → grounded shortlist
- `prompt injection page` → snapshot treated as untrusted; refuses to follow page instructions

`browser_click` and `browser_type` require user approval via the existing approval UI.

## What this app intentionally does not do yet

No OS-level computer use, unrestricted web crawling in CI, Telegram/Gmail/GitHub connectors, or multi-agent delegation.

## Environment note

If Electron starts as Node and `app` is undefined:

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dev
```
