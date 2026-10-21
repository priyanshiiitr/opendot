# AI Teammates

Local-first desktop AI teammate with a bounded agent loop, Playwright browser tools, connectors, durable automations, per-teammate computers, memory/skills/workflows, multi-agent teams, and an optional **always-on service + paired mobile companion** (Milestone 9).

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

## Milestone 9 — Always-on service & companion

The backend already hosts the durable worker. Run it **without Electron**:

```powershell
.\scripts\start-backend.ps1
```

Optional **current-user** Startup shortcut (never installed silently; not SYSTEM):

```powershell
.\scripts\register-user-startup.ps1          # enable
.\scripts\register-user-startup.ps1 -Remove  # disable
```

- Closing the desktop window does **not** cancel healthy background jobs.
- Jobs do **not** run when the PC is powered off.
- Remote access defaults to **disabled** (`AI_TEAMMATES_REMOTE_ACCESS_ENABLED=false`, bind `127.0.0.1`).

### Pair a phone (private network)

1. Put phone and PC on the same private VPN (e.g. Tailscale).
2. Set `AI_TEAMMATES_REMOTE_ACCESS_ENABLED=true` and bind to the private interface if needed (`AI_TEAMMATES_BIND_HOST`).
3. Add the companion origin to `AI_TEAMMATES_CORS_ORIGINS`.
4. Desktop → **Ops** → Generate pairing code.
5. Phone browser → open the UI → **Companion** → enter code (session token stored in `sessionStorage`).
6. Revoke anytime from **Ops**.

See ADR `docs/decisions/0011-always-on-remote-companion.md`. Web Push is not enabled in this milestone.

## Milestone 8 — Multi-agent teams

1. Create specialist teammates (Chat sidebar or API) with distinct role labels.
2. Sidebar → **Teams** → create a team (lead + member ids), limits default to `max_depth=1` (no recursive re-delegation).
3. Start a research→report plan; **Tick coordinator** runs ready subtasks through the real `AgentRuntime` (mock provider offline).
4. Inspect subtask statuses, structured results, coordination events, and the final report path under `data/team_artifacts/<run_id>/`.

Delegation never expands permissions. Plans with cycles, unknown deps, or unauthorized assignees are rejected.

See ADR `docs/decisions/0010-multi-agent-team-coordinator.md`.

## Milestone 7 — Memory, skills, grants, workflows

1. Sidebar → **Memory** — save scoped preferences (user/teammate/project/task). Secrets are rejected. Delete is soft-delete and permanent for retrieval.
2. Sidebar → **Skills** — browse builtin packages (`startup-research`, `website-audit`), review & enable per teammate. Skills cannot expand tool permissions.
3. Same panel → **Connector grants** — optionally restrict which Telegram operations a teammate may use (fail closed when a grant exists).
4. Sidebar → **Workflows** — create a run from an enabled skill; advance steps; complete execute after writing the expected workspace artifact; final status uses deterministic verification.
5. Chat still injects retrieved memories into the system prompt (bounded); skill category gates apply when set on the runtime.

See ADR `docs/decisions/0009-memory-skills-scoped-permissions-workflows.md`.

## Milestone 6 — Teammate computers

Each teammate gets a stable local computer under `%LOCALAPPDATA%\AITeammates\computers\<id>\` (`profile/`, `workspace/`, `artifacts/`).

1. Sidebar → **Computer** (select a teammate first).
2. **Start** / **Start headed** opens that teammate’s persistent Playwright profile (not everyday Chrome).
3. Chat can use `browser_*` and `workspace_*` tools on that computer only.
4. **Take over** pauses agent browser actions and opens the same profile headed; **Return control** refreshes state.
5. Workspace files appear in the Computer panel; **Clear browser data** requires confirmation (workspace kept).

Honest limits: not a full VM; site sessions may expire; backend must be running.

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
- `docs/decisions/0008-per-teammate-local-browser-computers.md`
- `docs/decisions/0009-memory-skills-scoped-permissions-workflows.md`
- `docs/decisions/0010-multi-agent-team-coordinator.md`
- `docs/decisions/0011-always-on-remote-companion.md`
- `docs/interview_guide.md`
