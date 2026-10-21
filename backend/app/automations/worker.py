"""Background worker: claims queued AutomationRuns and drives AgentRuntime."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..database import AgentRun, Automation, AutomationRun, Conversation, ExecutionEvent, Message
from ..providers import build_provider
from ..runtime import AgentRuntime
from .checkpoints import checkpoint_after_event, classify_interrupted_run, dump_checkpoint, load_checkpoint
from .service import AutomationService

logger = logging.getLogger(__name__)


class AutomationWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        runtime: AgentRuntime,
        service: AutomationService,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.runtime = runtime
        self.service = service
        self.worker_id = f"worker-{uuid.uuid4().hex[:10]}"
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._current_run_id: str | None = None

    async def start(self) -> None:
        if not self.settings.automation_worker_enabled:
            logger.info("Automation worker disabled by settings")
            return
        with self.session_factory() as db:
            self.reconcile_on_startup(db)
            self.service.process_startup_triggers(db)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="automation-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    def reconcile_on_startup(self, db: Session) -> None:
        """Mark crashed in-flight work without blindly replaying consequential tools."""
        # Only reconcile runs that were mid-execution. Queued work stays queued.
        for run in db.query(AutomationRun).filter(AutomationRun.status == "running").all():
            agent_status = None
            if run.agent_run_id:
                agent = db.get(AgentRun, run.agent_run_id)
                agent_status = agent.status if agent else None
            cp = load_checkpoint(run.checkpoint_json)
            new_status = classify_interrupted_run(cp, agent_status)
            run.status = new_status
            run.lease_owner = None
            run.lease_until = None
            if new_status == "needs_reconciliation":
                run.stop_reason = "Interrupted during a consequential tool; outcome uncertain — review before retry."
                run.error_message = run.stop_reason
                self.service.notify(
                    db,
                    "needs_reconciliation",
                    "Automation needs review",
                    run.id,
                    run.stop_reason or "",
                )
            elif new_status == "waiting_approval":
                run.stop_reason = "Waiting for approval after restart"
            else:
                run.status = "interrupted"
                run.stop_reason = "Interrupted by application restart"
            run.finished_at = run.finished_at or datetime.now(timezone.utc)
            db.commit()

        # Clear abandoned lease markers on queued rows (should be rare).
        for run in db.query(AutomationRun).filter(AutomationRun.status == "queued").all():
            if run.lease_owner:
                run.lease_owner = None
                run.lease_until = None
                db.commit()

        # Pending approvals must survive restart — leave waiting_approval untouched.
        for run in db.query(AutomationRun).filter(AutomationRun.status == "waiting_approval").all():
            run.lease_owner = None
            run.lease_until = None
            db.commit()

    async def _loop(self) -> None:
        scheduler_every = max(5.0, self.settings.automation_scheduler_tick_seconds)
        last_schedule = 0.0
        while not self._stop.is_set():
            try:
                loop_time = asyncio.get_running_loop().time()
                if loop_time - last_schedule >= scheduler_every:
                    with self.session_factory() as db:
                        self.service.process_due_schedules(db)
                    last_schedule = loop_time
                claimed = self._claim_next()
                if claimed:
                    await self._execute(claimed)
                else:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.settings.automation_worker_poll_seconds)
                    except asyncio.TimeoutError:
                        pass
            except Exception:
                logger.exception("Automation worker loop error")
                await asyncio.sleep(self.settings.automation_worker_poll_seconds)

    def _claim_next(self) -> str | None:
        """Atomically claim one queued run via conditional UPDATE (lease)."""
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=self.settings.automation_lease_seconds)
        with self.session_factory() as db:
            run = (
                db.query(AutomationRun)
                .filter(AutomationRun.status == "queued")
                .order_by(AutomationRun.created_at)
                .first()
            )
            if not run:
                return None
            result = db.execute(
                text(
                    "UPDATE automation_runs SET status='running', lease_owner=:owner, "
                    "lease_until=:lease, started_at=:started, updated_at=:started "
                    "WHERE id=:id AND status='queued'"
                ),
                {
                    "owner": self.worker_id,
                    "lease": lease_until.isoformat(),
                    "started": now.isoformat(),
                    "id": run.id,
                },
            )
            db.commit()
            if result.rowcount != 1:
                return None
            return run.id

    async def _execute(self, run_id: str) -> None:
        self._current_run_id = run_id
        try:
            await asyncio.wait_for(self._execute_inner(run_id), timeout=None)
        finally:
            self._current_run_id = None

    async def _execute_inner(self, run_id: str) -> None:
        with self.session_factory() as db:
            run = db.get(AutomationRun, run_id)
            if not run or run.status != "running":
                return
            auto = db.get(Automation, run.automation_id)
            if not auto:
                run.status = "failed"
                run.error_message = "Automation definition missing"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return

            conversation = Conversation(
                teammate_id=auto.teammate_id,
                title=f"Automation · {auto.name}"[:200],
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            run.conversation_id = conversation.id

            parts = [run.instruction_snapshot]
            if auto.include_previous_summary:
                summary = self.service.previous_summary(db, auto)
                if summary:
                    parts.append(summary)
            user_text = "\n\n".join(parts)
            db.add(Message(conversation_id=conversation.id, role="user", content=user_text))
            db.commit()

            # Temporarily apply per-automation limits on the shared settings object used by runtime.
            old_iter = self.settings.agent_max_iterations
            old_tools = self.settings.agent_max_tool_calls
            self.settings.agent_max_iterations = auto.max_iterations
            self.settings.agent_max_tool_calls = auto.max_tool_calls
            try:
                agent_run = self.runtime.start_run(db, conversation.id)
                run.agent_run_id = agent_run.id
                db.commit()

                provider = build_provider(self.settings)
                deadline = datetime.now(timezone.utc) + timedelta(seconds=auto.max_run_seconds)
                checkpoint = load_checkpoint(run.checkpoint_json)
                async for event in self.runtime.run(db, conversation, provider, agent_run):
                    if self._stop.is_set():
                        run.status = "interrupted"
                        run.stop_reason = "Worker shutting down"
                        run.finished_at = datetime.now(timezone.utc)
                        db.commit()
                        return
                    db.refresh(run)
                    if run.status == "cancelled" or (agent_run and agent_run.cancelled):
                        run.status = "cancelled"
                        run.stop_reason = "Cancelled by user"
                        run.finished_at = datetime.now(timezone.utc)
                        db.commit()
                        return
                    if datetime.now(timezone.utc) > deadline:
                        agent_run.cancelled = True
                        agent_run.status = "cancelled"
                        run.status = "failed"
                        run.error_message = "Maximum run duration exceeded"
                        run.stop_reason = run.error_message
                        run.finished_at = datetime.now(timezone.utc)
                        db.commit()
                        self.service.notify(db, "failed", "Automation timed out", run.id, run.error_message)
                        return

                    checkpoint = checkpoint_after_event(checkpoint, event)
                    run.checkpoint_json = dump_checkpoint(checkpoint)
                    run.progress_json = json.dumps(
                        {
                            "last_event": event.get("type"),
                            "tool_name": event.get("tool_name"),
                            "status": event.get("status"),
                        },
                        ensure_ascii=False,
                    )
                    # Heartbeat lease
                    run.lease_until = datetime.now(timezone.utc) + timedelta(seconds=self.settings.automation_lease_seconds)
                    db.commit()

                    if event.get("type") == "approval_required":
                        run.status = "waiting_approval"
                        run.stop_reason = "Waiting for user approval"
                        db.commit()
                        self.service.notify(
                            db,
                            "waiting_approval",
                            "Automation needs approval",
                            run.id,
                            event.get("tool_name") or "approval required",
                        )
                        return
                    if event.get("type") == "error":
                        run.status = "failed"
                        run.error_message = event.get("message") or "Agent error"
                        run.stop_reason = run.error_message
                        run.finished_at = datetime.now(timezone.utc)
                        db.commit()
                        self.service.notify(db, "failed", "Automation failed", run.id, run.error_message or "")
                        return
                    if event.get("type") == "done":
                        run.status = "completed"
                        run.stop_reason = "Completed"
                        run.finished_at = datetime.now(timezone.utc)
                        db.commit()
                        self.service.notify(db, "completed", f"Automation finished: {auto.name}", run.id, "")
                        return

                # Stream ended without terminal — treat as interrupted
                if run.status == "running":
                    run.status = "interrupted"
                    run.stop_reason = "Agent stream ended unexpectedly"
                    run.finished_at = datetime.now(timezone.utc)
                    db.commit()
            finally:
                self.settings.agent_max_iterations = old_iter
                self.settings.agent_max_tool_calls = old_tools

    async def resume_after_approval(self, run_id: str, approval_id: str, decision: str) -> None:
        """Continue a waiting_approval automation run via AgentRuntime.resolve_approval."""
        with self.session_factory() as db:
            run = db.get(AutomationRun, run_id)
            if not run or run.status != "waiting_approval" or not run.conversation_id:
                raise RuntimeError("Run is not waiting for approval.")
            conversation = db.get(Conversation, run.conversation_id)
            if not conversation:
                raise RuntimeError("Conversation missing for automation run.")
            auto = db.get(Automation, run.automation_id)
            run.status = "running"
            run.lease_owner = self.worker_id
            run.lease_until = datetime.now(timezone.utc) + timedelta(seconds=self.settings.automation_lease_seconds)
            db.commit()
            provider = build_provider(self.settings)
            checkpoint = load_checkpoint(run.checkpoint_json)
            async for event in self.runtime.resolve_approval(db, conversation, approval_id, decision, provider):
                checkpoint = checkpoint_after_event(checkpoint, event)
                run.checkpoint_json = dump_checkpoint(checkpoint)
                run.progress_json = json.dumps({"last_event": event.get("type")}, ensure_ascii=False)
                db.commit()
                if event.get("type") == "approval_required":
                    run.status = "waiting_approval"
                    db.commit()
                    self.service.notify(db, "waiting_approval", "Automation needs approval", run.id, "")
                    return
                if event.get("type") == "error":
                    run.status = "failed"
                    run.error_message = event.get("message")
                    run.stop_reason = run.error_message
                    run.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    self.service.notify(db, "failed", "Automation failed", run.id, run.error_message or "")
                    return
                if event.get("type") == "done":
                    run.status = "completed"
                    run.stop_reason = "Completed after approval"
                    run.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    name = auto.name if auto else "Automation"
                    self.service.notify(db, "completed", f"Automation finished: {name}", run.id, "")
                    return
            if run.status == "running":
                run.status = "interrupted"
                run.stop_reason = "Approval resume ended unexpectedly"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
