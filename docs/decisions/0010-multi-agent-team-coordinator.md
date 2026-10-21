# ADR 0010 — Bounded multi-agent team coordinator

## Status

Accepted (Milestone 8)

## Context

M7 delivered single-agent workflows, memory, and skills. Users need configurable teams where a lead delegates real subtasks to other teammates — not role-play inside one response — without a second agent framework.

## Decision

1. **Team** roster rows bind a lead + member teammate IDs with explicit limits (`max_subtasks`, `max_concurrent`, `max_depth=1`, retries).
2. **TeamRun / TeamSubtask / TeamEvent** persist parent/child state and auditable coordination facts.
3. **Plan validation** (`validate_plan`) rejects cycles, unknown deps, unauthorized assignees, depth/count violations *before* scheduling.
4. **TeamCoordinator** schedules ready subtasks (dependency-aware), executes each via the existing **AgentRuntime** on a fresh conversation with minimal context, publishes structured results + shared artifacts, then finalizes with an honest report.
5. **Least privilege:** subtasks carry `allowed_tool_categories`; PermissionBroker restricts only — delegation never expands connectors/accounts.
6. **Shared AgentRuntime lock:** concurrent in-flight subtasks are bounded, but model/tool turns serialize on one runtime to avoid ContextVar races.

## Alternatives considered

- Multi-agent frameworks (LangGraph, AutoGen) — heavier, harder to teach, duplicates our runtime.
- Copying full parent chat into every specialist — wastes context and leaks private history.
- Unlimited recursive delegation — rejected; `max_depth` defaults to 1.

## Consequences

- Sequential delegation is the reliable default (`max_concurrent=1`).
- Restart marks in-flight subtasks `needs_reconciliation`.
- UI **Teams** panel creates plans and ticks the coordinator; no decorative controls.
