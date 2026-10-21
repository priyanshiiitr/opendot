# ADR 0007 — Durable automations on SQLite + lifespan worker

## Status

Accepted (Milestone 5)

## Context

Chat agent runs were bound to HTTP SSE. Closing the UI or navigating away ended the generator even though approval state lived in SQLite. Milestone 5 needs scheduled and webhook-triggered work that continues without an open chat stream.

## Decision

1. **SQLite job rows** (`automations`, `automation_runs`) instead of Celery/Redis — appropriate for a single-user desktop workspace.
2. **FastAPI lifespan worker** polls/claims `queued` runs with a conditional SQL `UPDATE … WHERE status='queued'`, then drives the existing `AgentRuntime` on a **dedicated conversation** per run (clean context by default).
3. **Checkpoints** after significant events; consequential tools (`telegram_send_message`, browser mutations) mark `uncertain_side_effect`. On restart those runs become `needs_reconciliation` rather than silent replay.
4. **Pending approvals** stay `waiting_approval` across restart; never auto-approved.
5. **Schedules** compute `next_run_at` in a documented timezone (`zoneinfo`); `occurrence_key` uniqueness prevents duplicate enqueue from ticks/restarts. Missed policy defaults to **skip catch-up** (no flood after days offline).
6. **Webhooks** on `/hooks/{id}` (not `/api`), bearer secret hashed at rest, body size + rate limits, event-id dedupe. Loopback by default.
7. **Schema evolution**: versioned `schema_migrations` runner + additive `create_all`. Full Alembic deferred until multi-host deploy needs it; existing conversation data is preserved.

## Alternatives considered

| Option | Why not now |
| --- | --- |
| Celery + Redis | Extra processes/infra for one desktop user |
| Detached `asyncio.create_task` only | Not durable across crash; no lease/claim |
| Reuse chat conversation history for every schedule | Pollutes context; unbounded tokens |
| Blind resume of all `running` rows | Risk of duplicate Telegram sends |

## Consequences

- Local scheduling requires the backend process to be running; offline time follows missed-run policy.
- Browser tools still share one Playwright session — parallel automations may serialize on the browser lock.
- UI polls run receipts; a closed SSE is not treated as success.
