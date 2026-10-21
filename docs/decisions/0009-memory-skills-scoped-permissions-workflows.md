# ADR 0009 — Persistent memory, versioned skills, scoped permissions, single-agent workflows

## Status

Accepted (Milestone 7)

## Context

After M5 durable automations and M6 per-teammate computers, the product needed inspectable long-term memory, reusable skill packages, account/resource-scoped connector grants, and multi-step workflow state — without multi-agent delegation or a second agent runtime.

## Decision

1. **Memory** — SQLite `memories` table + `MemoryService` with scopes (`user` / `teammate` / `project` / `task`), explicit vs inferred source, soft-delete, supersede-on-correct, secret heuristics, and bounded keyword retrieval injected into the system prompt (not full history).
2. **Skills** — Local versioned packages (`SKILL.md` + frontmatter) under `skills_root_dir`, seeded from `app/skills/builtin/`. Install rows require review before enable. Skills declare preferred tool *categories*; `PermissionBroker` enforces them as a restrict-only gate.
3. **Permissions** — Extend `PermissionBroker` with `AuthContext` + `ConnectorGrant` rows. When a grant exists for a teammate/connector, operations must be listed. No grant keeps legacy connector rules (Telegram allowlist + approval).
4. **Workflows** — `WorkflowRun` / `WorkflowStep` with dependency ordering, checkpoints, safe-tool retry set, and deterministic artifact verification via skill expected outputs. Advance API runs without paid models; execute step completes only when the caller confirms artifacts exist.

## Alternatives considered

- Vector DB for memory — deferred until keyword retrieval is insufficient.
- Skills that auto-install deps / run scripts — rejected (untrusted input).
- Replacing PermissionBroker — rejected; extend in place.
- Interpreting model prose as workflow completion — rejected; verification is deterministic.

## Consequences

- Chat continues to use `AgentRuntime`; workflows reuse skill metadata + workspace verification.
- Historical runs store `skill_version` and `memory_ids` for provenance.
- Migrations are additive (`2026_10_m7_memory_skills_workflows`); no DB reset.
