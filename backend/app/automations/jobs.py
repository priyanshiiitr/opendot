"""Durable job contract — what AutomationWorker already satisfies.

External runtimes (Trigger.dev-style) can implement DurableExecutionBackend later
without changing AgentRuntime callers. Default remains in-process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class JobEnvelope:
    """Stable description of one unit of durable agent work."""

    job_type: str  # e.g. automation_run
    run_id: str
    teammate_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_type": self.job_type,
            "run_id": self.run_id,
            "teammate_id": self.teammate_id,
            "payload": dict(self.payload),
            "idempotency_key": self.idempotency_key,
        }


@runtime_checkable
class DurableExecutionBackend(Protocol):
    """Lifecycle for the process that claims and runs durable jobs."""

    backend_name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def describe(self) -> dict[str, Any]: ...


class ExternalDurableNotConfigured(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# Contract notes for a future Trigger.dev (or equivalent) adapter:
# - enqueue(JobEnvelope) must be idempotent when idempotency_key is set
# - worker must claim with a lease (same semantics as AutomationRun.lease_*)
# - consequential tool interruption → needs_reconciliation (never blind replay)
# - approvals survive process restart
# - secrets stay on the control plane; sandbox never receives model API keys by default
FUTURE_EXTERNAL_BACKEND_REQUIREMENTS = (
    "idempotent_enqueue",
    "leased_claim",
    "checkpoint_reconciliation",
    "approval_persistence",
    "secret_isolation",
)
