"""Validated multi-step single-agent workflows with checkpoints and verification."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..database import WorkflowRun, WorkflowStep
from ..skills.loader import SkillPackage


class WorkflowError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


SAFE_RETRY_TOOLS = frozenset(
    {
        "get_current_time",
        "calculate",
        "browser_snapshot",
        "browser_extract",
        "workspace_list",
        "workspace_read",
        "memory_list",
        "telegram_get_bot_info",
        "telegram_get_recent_updates",
    }
)


class WorkflowService:
    def create(
        self,
        db: Session,
        *,
        teammate_id: int,
        title: str,
        instruction: str,
        steps: list[dict[str, Any]],
        skill_id: str | None = None,
        skill_version: str | None = None,
        memory_ids: list[str] | None = None,
        conversation_id: int | None = None,
    ) -> WorkflowRun:
        if not steps:
            raise WorkflowError("Workflow requires at least one step.")
        if len(steps) > 40:
            raise WorkflowError("Too many workflow steps.")
        keys = set()
        for step in steps:
            key = str(step.get("step_key") or "").strip()
            if not key or key in keys:
                raise WorkflowError("Each step needs a unique step_key.")
            keys.add(key)
        run = WorkflowRun(
            id=str(uuid.uuid4()),
            teammate_id=teammate_id,
            conversation_id=conversation_id,
            title=(title or "Workflow")[:200],
            instruction=instruction or "",
            skill_id=skill_id,
            skill_version=skill_version,
            status="pending",
            memory_ids_json=json.dumps(memory_ids or []),
        )
        db.add(run)
        db.flush()
        for step in steps:
            db.add(
                WorkflowStep(
                    id=str(uuid.uuid4()),
                    workflow_id=run.id,
                    step_key=str(step["step_key"]),
                    description=str(step.get("description") or ""),
                    depends_on_json=json.dumps(step.get("depends_on") or []),
                    tool_name=step.get("tool_name"),
                    status="pending",
                )
            )
        db.commit()
        db.refresh(run)
        return run

    def plan_from_skill(self, skill: SkillPackage, instruction: str) -> list[dict[str, Any]]:
        """Deterministic skeleton plan from skill metadata (validated before execution)."""
        steps = [
            {
                "step_key": "load_context",
                "description": f"Load skill {skill.skill_id}@{skill.version} and relevant memories",
                "depends_on": [],
                "tool_name": "memory_list",
            },
            {
                "step_key": "execute",
                "description": skill.description or "Execute skill instructions with authorized tools",
                "depends_on": ["load_context"],
                "tool_name": None,
            },
            {
                "step_key": "verify_outputs",
                "description": "Verify expected workspace artifacts exist",
                "depends_on": ["execute"],
                "tool_name": "workspace_list",
            },
        ]
        _ = instruction
        return steps

    def get(self, db: Session, workflow_id: str) -> WorkflowRun:
        row = db.get(WorkflowRun, workflow_id)
        if not row:
            raise WorkflowError("Workflow not found.")
        return row

    def steps(self, db: Session, workflow_id: str) -> list[WorkflowStep]:
        return (
            db.query(WorkflowStep)
            .filter(WorkflowStep.workflow_id == workflow_id)
            .order_by(WorkflowStep.created_at)
            .all()
        )

    def next_eligible(self, db: Session, workflow_id: str) -> WorkflowStep | None:
        steps = self.steps(db, workflow_id)
        done = {s.step_key for s in steps if s.status == "completed"}
        for step in steps:
            if step.status != "pending":
                continue
            deps = json.loads(step.depends_on_json or "[]")
            if all(d in done for d in deps):
                return step
        return None

    def mark_running(self, db: Session, step: WorkflowStep) -> None:
        step.status = "running"
        step.attempts += 1
        step.started_at = datetime.now(timezone.utc)
        wf = db.get(WorkflowRun, step.workflow_id)
        if wf:
            wf.status = "running"
        db.commit()

    def complete_step(
        self,
        db: Session,
        step: WorkflowStep,
        *,
        output: dict[str, Any] | None = None,
        artifact_path: str | None = None,
    ) -> None:
        step.status = "completed"
        step.output_json = json.dumps(output or {}, ensure_ascii=False)
        step.artifact_path = artifact_path
        step.finished_at = datetime.now(timezone.utc)
        step.checkpoint_json = json.dumps({"safe_to_resume": True, "completed": True})
        db.commit()

    def fail_step(self, db: Session, step: WorkflowStep, error: str, *, uncertain: bool = False) -> None:
        step.status = "needs_reconciliation" if uncertain else "failed"
        step.error_message = error
        step.finished_at = datetime.now(timezone.utc)
        step.checkpoint_json = json.dumps({"safe_to_resume": not uncertain, "uncertain_side_effect": uncertain})
        wf = db.get(WorkflowRun, step.workflow_id)
        if wf:
            wf.status = step.status
        db.commit()

    def waiting_approval(self, db: Session, step: WorkflowStep) -> None:
        step.status = "waiting_approval"
        wf = db.get(WorkflowRun, step.workflow_id)
        if wf:
            wf.status = "waiting_approval"
        db.commit()

    def can_retry(self, step: WorkflowStep) -> bool:
        if step.attempts >= 3:
            return False
        return (step.tool_name or "") in SAFE_RETRY_TOOLS

    def verify_skill_outputs(self, skill: SkillPackage, workspace_root: Path) -> dict[str, Any]:
        """Deterministic completion checks — not model fluency."""
        checks: list[dict[str, Any]] = []
        ok = True
        for expected in skill.expected_outputs:
            # Convention: markdown report names
            candidates = []
            if "markdown" in expected.lower() or "report" in expected.lower():
                candidates = list(workspace_root.glob("*.md"))
            if "audit" in expected.lower():
                candidates = list(workspace_root.glob("*audit*.md")) or candidates
            if "startup" in skill.skill_id:
                specific = workspace_root / "startup-research-report.md"
                present = specific.is_file()
                checks.append({"criterion": "startup-research-report.md exists", "passed": present})
                ok = ok and present
                if present and "Sources" not in specific.read_text(encoding="utf-8", errors="replace"):
                    checks.append({"criterion": "Sources section present", "passed": False})
                    ok = False
                else:
                    checks.append({"criterion": "Sources section present", "passed": present})
            elif "audit" in skill.skill_id:
                specific = workspace_root / "website-audit.md"
                present = specific.is_file()
                checks.append({"criterion": "website-audit.md exists", "passed": present})
                ok = ok and present
            else:
                present = bool(candidates)
                checks.append({"criterion": f"output for {expected}", "passed": present})
                ok = ok and present
        for criterion in skill.evaluation_criteria:
            if "exists" in criterion.lower() and not any(c.get("passed") for c in checks):
                checks.append({"criterion": criterion, "passed": False})
                ok = False
            else:
                checks.append({"criterion": criterion, "passed": True, "note": "manual/soft criterion"})
        return {"passed": ok, "checks": checks}

    def advance_deterministic(
        self,
        db: Session,
        workflow_id: str,
        *,
        workspace_root: Path | None = None,
        skill: SkillPackage | None = None,
        complete_execute: bool = False,
        execute_output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance one eligible step without calling a paid model.

        Used by the API/worker for safe steps and by tests. The ``execute`` step
        only completes when ``complete_execute=True`` (caller already produced artifacts).
        """
        wf = self.get(db, workflow_id)
        step = self.next_eligible(db, workflow_id)
        if step is None:
            steps = self.steps(db, workflow_id)
            if all(s.status == "completed" for s in steps):
                verification = {"passed": True, "checks": []}
                if skill and workspace_root:
                    verification = self.verify_skill_outputs(skill, workspace_root)
                report = {
                    "completed_steps": [s.step_key for s in steps if s.status == "completed"],
                    "failed_steps": [],
                    "verification": verification,
                    "memory_ids": json.loads(wf.memory_ids_json or "[]"),
                    "skill_id": wf.skill_id,
                    "skill_version": wf.skill_version,
                }
                self.finalize(db, workflow_id, report)
                return self.public(db, self.get(db, workflow_id))
            return self.public(db, wf)

        self.mark_running(db, step)
        tool = step.tool_name or ""
        try:
            if step.step_key == "execute" or (tool == "" and step.step_key not in {"load_context", "verify_outputs"}):
                if not complete_execute:
                    # Leave running → roll back to pending so a later resume can pick it up,
                    # or keep as running for agent handoff. For API, mark pending again.
                    step.status = "pending"
                    step.attempts = max(0, step.attempts - 1)
                    wf.status = "running"
                    db.commit()
                    pub = self.public(db, wf)
                    pub["awaiting"] = "execute"
                    return pub
                self.complete_step(db, step, output=execute_output or {"note": "execute completed"})
            elif tool in {"memory_list", ""} and step.step_key == "load_context":
                self.complete_step(
                    db,
                    step,
                    output={"memory_ids": json.loads(wf.memory_ids_json or "[]"), "loaded": True},
                )
            elif tool == "workspace_list" or step.step_key == "verify_outputs":
                verification = {"passed": True, "checks": []}
                if skill and workspace_root:
                    verification = self.verify_skill_outputs(skill, workspace_root)
                if verification.get("passed"):
                    self.complete_step(db, step, output=verification)
                else:
                    step.status = "failed"
                    step.output_json = json.dumps(verification, ensure_ascii=False)
                    step.error_message = "Expected workspace artifacts missing or incomplete."
                    step.finished_at = datetime.now(timezone.utc)
                    wf.status = "failed"
                    db.commit()
            elif tool in SAFE_RETRY_TOOLS:
                self.complete_step(db, step, output={"ok": True, "tool": tool})
            else:
                # Consequential / unknown — do not auto-retry; require reconciliation if forced.
                self.fail_step(db, step, f"Step tool '{tool}' is not auto-advancable.", uncertain=True)
        except Exception as exc:  # noqa: BLE001 — surface into step error
            if self.can_retry(step) and tool in SAFE_RETRY_TOOLS:
                step.status = "pending"
                db.commit()
            else:
                uncertain = tool not in SAFE_RETRY_TOOLS and tool != ""
                self.fail_step(db, step, str(exc), uncertain=uncertain)

        # If all done, finalize with verification
        steps = self.steps(db, workflow_id)
        if all(s.status == "completed" for s in steps):
            verification = {"passed": True, "checks": []}
            if skill and workspace_root:
                verification = self.verify_skill_outputs(skill, workspace_root)
            report = {
                "completed_steps": [s.step_key for s in steps],
                "failed_steps": [],
                "skipped_steps": [],
                "verification": verification,
                "memory_ids": json.loads(wf.memory_ids_json or "[]"),
                "skill_id": wf.skill_id,
                "skill_version": wf.skill_version,
            }
            self.finalize(db, workflow_id, report)
        return self.public(db, self.get(db, workflow_id))

    def finalize(self, db: Session, workflow_id: str, report: dict[str, Any]) -> WorkflowRun:
        wf = self.get(db, workflow_id)
        steps = self.steps(db, workflow_id)
        if any(s.status in {"failed", "needs_reconciliation"} for s in steps):
            wf.status = "failed" if any(s.status == "failed" for s in steps) else "needs_reconciliation"
        elif any(s.status == "waiting_approval" for s in steps):
            wf.status = "waiting_approval"
        elif all(s.status == "completed" for s in steps):
            wf.status = "completed" if report.get("verification", {}).get("passed", False) else "failed"
        else:
            wf.status = "running"
        wf.report_json = json.dumps(report, ensure_ascii=False)
        wf.finished_at = datetime.now(timezone.utc) if wf.status in {"completed", "failed", "needs_reconciliation"} else None
        db.commit()
        db.refresh(wf)
        return wf

    def public(self, db: Session, wf: WorkflowRun) -> dict[str, Any]:
        return {
            "id": wf.id,
            "teammate_id": wf.teammate_id,
            "conversation_id": wf.conversation_id,
            "title": wf.title,
            "instruction": wf.instruction,
            "skill_id": wf.skill_id,
            "skill_version": wf.skill_version,
            "status": wf.status,
            "memory_ids": json.loads(wf.memory_ids_json or "[]"),
            "report": json.loads(wf.report_json or "{}"),
            "steps": [
                {
                    "id": s.id,
                    "step_key": s.step_key,
                    "description": s.description,
                    "depends_on": json.loads(s.depends_on_json or "[]"),
                    "status": s.status,
                    "tool_name": s.tool_name,
                    "attempts": s.attempts,
                    "output": json.loads(s.output_json or "{}"),
                    "artifact_path": s.artifact_path,
                    "error_message": s.error_message,
                    "checkpoint": json.loads(s.checkpoint_json or "{}"),
                    "started_at": s.started_at,
                    "finished_at": s.finished_at,
                }
                for s in self.steps(db, wf.id)
            ],
            "created_at": wf.created_at,
            "updated_at": wf.updated_at,
            "finished_at": wf.finished_at,
        }
