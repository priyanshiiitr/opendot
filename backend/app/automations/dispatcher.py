"""Thin dispatcher over durable execution backends.

Callers start/stop via JobDispatcher. Today only in_process is active.
An external Trigger.dev-style backend can be registered later without
changing AutomationService or AgentRuntime.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .jobs import (
    FUTURE_EXTERNAL_BACKEND_REQUIREMENTS,
    DurableExecutionBackend,
    ExternalDurableNotConfigured,
    JobEnvelope,
)
from .trigger_http import TriggerHttpDurableBackend
from .worker import AutomationWorker

# Re-export for existing tests/importers
__all__ = [
    "ExternalDurableNotConfigured",
    "JobDispatcher",
    "InProcessDurableBackend",
    "ExternalDurableBackendStub",
    "TriggerHttpDurableBackend",
]


class InProcessDurableBackend:
    """Wraps the existing AutomationWorker — SQLite queue + lifespan loop."""

    backend_name = "in_process"

    def __init__(self, worker: AutomationWorker):
        self._worker = worker

    async def start(self) -> None:
        await self._worker.start()

    async def stop(self) -> None:
        await self._worker.stop()

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "worker_id": self._worker.worker_id,
            "enabled": self._worker.settings.automation_worker_enabled,
            "queue": "sqlite AutomationRun",
            "note": "Default durable path. Work continues while the backend process is up.",
        }


class ExternalDurableBackendStub:
    """Placeholder for Trigger.dev / Cloud Tasks / etc. Never enqueues remotely."""

    backend_name = "external_stub"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def start(self) -> None:
        if not self.settings.durable_external_backend_enabled:
            raise ExternalDurableNotConfigured(
                "External durable backend is disabled. "
                "Keep AI_TEAMMATES_DURABLE_EXTERNAL_BACKEND_ENABLED=false unless you "
                "intentionally wire Trigger.dev (or equivalent) later."
            )
        raise ExternalDurableNotConfigured(
            "External durable backend stub is enabled but not implemented. "
            f"Required capabilities: {', '.join(FUTURE_EXTERNAL_BACKEND_REQUIREMENTS)}. "
            "No remote jobs were created."
        )

    async def stop(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "enabled_flag": self.settings.durable_external_backend_enabled,
            "implemented": False,
            "future_requirements": list(FUTURE_EXTERNAL_BACKEND_REQUIREMENTS),
            "note": "Stub only — in_process remains the active runner.",
        }

    async def enqueue(self, job: JobEnvelope) -> str:
        _ = job
        raise ExternalDurableNotConfigured(
            "Cannot enqueue to external durable backend stub. Use in_process."
        )


class JobDispatcher:
    """Selects the durable backend. Default: in_process (existing worker)."""

    def __init__(self, settings: Settings, worker: AutomationWorker):
        self.settings = settings
        self.worker = worker
        self.in_process = InProcessDurableBackend(worker)
        self.external_stub = ExternalDurableBackendStub(settings)
        self.trigger_http = TriggerHttpDurableBackend(settings)

    @property
    def active_name(self) -> str:
        name = (self.settings.durable_execution_backend or "in_process").strip()
        return name or "in_process"

    def active(self) -> DurableExecutionBackend:
        if self.active_name == "external_stub":
            return self.external_stub
        if self.active_name == "trigger_http":
            return self.trigger_http
        if self.active_name != "in_process":
            raise ExternalDurableNotConfigured(
                f"Unknown durable backend '{self.active_name}'. "
                "Supported: in_process (default), external_stub, trigger_http."
            )
        return self.in_process

    async def start(self) -> None:
        await self.active().start()
        # When using trigger_http, keep in-process worker available for callback execution
        # unless explicitly disabled via automation_worker_enabled=false.
        if self.active_name == "trigger_http" and self.settings.automation_worker_enabled:
            try:
                await self.in_process.start()
            except Exception:
                pass

    async def stop(self) -> None:
        # Always try to stop in-process worker if it was started; external stub is no-op.
        try:
            await self.active().stop()
        except ExternalDurableNotConfigured:
            await self.in_process.stop()
        if self.active_name == "trigger_http":
            await self.in_process.stop()

    async def enqueue(self, job: JobEnvelope) -> str:
        """Enqueue on the active backend. trigger_http / external_stub implement enqueue."""
        backend = self.active()
        enqueue = getattr(backend, "enqueue", None)
        if enqueue is None:
            raise ExternalDurableNotConfigured(
                f"Backend '{self.active_name}' does not support remote enqueue. "
                "Use trigger_http or keep in_process AutomationService.enqueue."
            )
        return await enqueue(job)

    def describe(self) -> dict[str, Any]:
        try:
            active = self.active().describe()
        except ExternalDurableNotConfigured as exc:
            active = {"error": exc.reason}
        return {
            "active": self.active_name,
            "backends": {
                "in_process": self.in_process.describe(),
                "external_stub": self.external_stub.describe(),
                "trigger_http": self.trigger_http.describe(),
            },
            "active_detail": active,
            "llm_note": "Model provider (e.g. Groq free tier) stays on the control plane via AI_TEAMMATES_MODEL_PROVIDER.",
        }
