# ADR 0002: Backend-owned, bounded tool execution

## Status

Accepted for Milestone 2.

## Decision

Tool requests are structured provider outputs, but the FastAPI backend owns existence checks, validation, authorization, execution, limits, and persistence. The React renderer only displays events and posts an explicit approval decision.

## Alternatives considered

1. **Let the model emit shell/Python and execute it** — rejected: unbounded code execution.
2. **Parse free-form text for “CALL calculate(...)”** — rejected: ambiguous and forgeable.
3. **Adopt a full agent framework early** — deferred: harder to inspect and teach; a thin explicit loop is enough for three local tools.

## Consequences

- Easy to test with a mock provider that must consume real tool results.
- Approval is a durable state transition on `ExecutionEvent` / `AgentRun`.
- Production will still need migrations, stronger worker isolation, and richer observability.
