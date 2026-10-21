"""Computer provider abstraction — local browser, isolated Linux-dev, future billable cloud."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ComputerProvider(Protocol):
    """Per-teammate execution environment. Model never calls the cloud control plane."""

    provider_name: str
    isolation_tier: str  # local_browser | filesystem_sandbox | container | vm | managed_cloud

    async def provision(self) -> dict[str, Any]: ...

    async def start(self, *, headed: bool = False) -> dict[str, Any]: ...

    async def stop(self) -> dict[str, Any]: ...

    async def health(self) -> dict[str, Any]: ...

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]: ...

    async def close_browser(self) -> dict[str, Any]: ...

    async def status(self) -> dict[str, Any]: ...

    async def preview(self) -> bytes: ...

    async def enter_human_control(self) -> dict[str, Any]: ...

    async def release_human_control(self) -> dict[str, Any]: ...

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]: ...

    def workspace_paths(self) -> dict[str, str]: ...


# Takeover / control plane states (backend-owned)
CONTROL_STATES = frozenset(
    {
        "agent_control",
        "pause_requested",
        "waiting_for_safe_point",
        "human_control",
        "returning_to_agent",
        "reconciliation_required",
    }
)

LIFECYCLE_STATES = frozenset(
    {
        "provisioning",
        "stopped",
        "starting",
        "ready",
        "busy",
        "paused",
        "waiting_human",
        "human_control",
        "recovering",
        "unavailable",
        "failed",
        "deleting",
        "deleted",
        "error",
    }
)
