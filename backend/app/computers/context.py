"""Request-scoped computer / teammate binding for tool execution."""

from __future__ import annotations

from contextvars import ContextVar

# Set by AgentRuntime for the duration of a tool-using turn. Tools must not trust model-supplied IDs.
current_teammate_id: ContextVar[int | None] = ContextVar("current_teammate_id", default=None)
current_computer_id: ContextVar[str | None] = ContextVar("current_computer_id", default=None)
