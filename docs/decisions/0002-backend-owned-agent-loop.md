# ADR 0002: Backend-owned, bounded tool execution

Tool requests are structured provider outputs, but the FastAPI backend owns existence, validation, authorization, execution, and limits. The React renderer only displays events and posts an explicit approval decision.

We chose a small explicit loop over an agent framework. It is easy to inspect and makes the safety boundary visible. SQLite stores `AgentRun` and `ExecutionEvent` rows alongside messages; a production version would use database migrations and stronger cancellation/worker isolation.
