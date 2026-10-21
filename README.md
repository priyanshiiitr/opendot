# AI Teammates

Local-first desktop AI teammate with a bounded agent loop, Playwright browser tools, a **Telegram Bot API connector**, and a **durable Automations** worker (Milestone 5).

## Prerequisites

- Windows 10/11
- Node.js 20+ and npm
- Python 3.11+ (this workspace uses Python 3.14 with `.venv`)

## Setup

```powershell
npm install
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m playwright install chromium
Copy-Item backend\.env.example backend\.env
```

Providers: `mock` (offline), `groq` (free-tier Groq Cloud keys from console.groq.com), or `xai`.

Telegram bot tokens are entered only in the **Connections** UI and stored in **Windows Credential Manager** via `keyring`. Never put bot tokens in chat, git, or `.env`.

## Run

```powershell
# Terminal 1 — backend must stay up for schedules/webhooks
.\.venv\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dev
```

## Verify

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
cd ..
npm test
npm run build
```

## Milestone 5 — Automations

Open **Automations** in the sidebar.

- Create an automation with an instruction (uses the real `AgentRuntime` + tools).
- **Run now** queues a durable job; you can leave chat — the lifespan worker continues.
- Schedules: daily / weekdays / interval (timezone `Asia/Calcutta` by default).
- Approvals pause the run as `waiting_approval` and survive restart.
- Webhooks: create a hook, store the one-time bearer secret, `POST http://127.0.0.1:8000/hooks/{id}`.
- Receipts show verified tool events, not just model claims.

**Honest limits:** if the PC or backend is off, jobs wait until restart (missed-run policy skips catch-up floods). This is not a cloud always-on service.

### Demo (scheduled + recovery)

1. Create automation: instruction `add 2 and 3`, schedule daily 09:00, or use **Run now**.
2. Confirm run history shows `completed` and the assistant text mentions `5`.
3. Pause the automation — scheduler must not enqueue.
4. For recovery: a run interrupted mid-consequential tool is marked `needs_reconciliation` (see tests / ADR 0007).

## Milestone 4 — Telegram Bot

1. BotFather → token → Connections → Save & verify.
2. Allowlist your chat id; sends require **Approve send**.

## Earlier milestones

- M2: `add 12 plus 30`, `request approval`
- M3: fixture browser research (tests); public browsing uses fail-closed URL policy

## Docs

- `docs/architecture.md`
- `docs/roadmap.md`
- `docs/decisions/0007-durable-automations-sqlite-worker.md`
- `docs/interview_guide.md`
