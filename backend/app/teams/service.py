"""Team roster + run/subtask persistence and public views."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..database import Team, TeamEvent, TeamRun, TeamSubtask, Teammate
from .validation import PlanLimits, PlanValidationError, validate_plan


class TeamError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class TeamService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create_team(self, db: Session, data: dict[str, Any]) -> Team:
        lead_id = int(data["lead_teammate_id"])
        lead = db.get(Teammate, lead_id)
        if not lead or not lead.enabled:
            raise TeamError("Lead teammate must exist and be enabled.")
        members = [int(x) for x in data.get("member_ids") or []]
        if lead_id not in members:
            members.insert(0, lead_id)
        for mid in members:
            m = db.get(Teammate, mid)
            if not m:
                raise TeamError(f"Unknown teammate id {mid}.")
        team = Team(
            id=str(uuid.uuid4()),
            name=str(data.get("name") or "Team")[:120],
            description=str(data.get("description") or "")[:2000],
            lead_teammate_id=lead_id,
            member_ids_json=json.dumps(members),
            enabled=bool(data.get("enabled", True)),
            max_subtasks=int(data.get("max_subtasks") or self.settings.team_max_subtasks),
            max_concurrent=int(data.get("max_concurrent") or self.settings.team_max_concurrent),
            max_depth=int(data.get("max_depth") or self.settings.team_max_depth),
            max_retries=int(data.get("max_retries") or self.settings.team_max_retries),
            max_total_runs=int(data.get("max_total_runs") or self.settings.team_max_total_runs),
        )
        db.add(team)
        db.commit()
        db.refresh(team)
        return team

    def list_teams(self, db: Session) -> list[Team]:
        return db.query(Team).order_by(Team.created_at.desc()).all()

    def get_team(self, db: Session, team_id: str) -> Team:
        team = db.get(Team, team_id)
        if not team:
            raise TeamError("Team not found.")
        return team

    def update_team(self, db: Session, team_id: str, patch: dict[str, Any]) -> Team:
        team = self.get_team(db, team_id)
        if "name" in patch and patch["name"] is not None:
            team.name = str(patch["name"])[:120]
        if "description" in patch and patch["description"] is not None:
            team.description = str(patch["description"])[:2000]
        if "enabled" in patch and patch["enabled"] is not None:
            team.enabled = bool(patch["enabled"])
        if "member_ids" in patch and patch["member_ids"] is not None:
            members = [int(x) for x in patch["member_ids"]]
            if team.lead_teammate_id not in members:
                members.insert(0, team.lead_teammate_id)
            for mid in members:
                if not db.get(Teammate, mid):
                    raise TeamError(f"Unknown teammate id {mid}.")
            team.member_ids_json = json.dumps(members)
        for field in ("max_subtasks", "max_concurrent", "max_depth", "max_retries", "max_total_runs"):
            if field in patch and patch[field] is not None:
                setattr(team, field, int(patch[field]))
        db.commit()
        db.refresh(team)
        return team

    def member_ids(self, team: Team) -> list[int]:
        return [int(x) for x in json.loads(team.member_ids_json or "[]")]

    def create_run(
        self,
        db: Session,
        *,
        team_id: str,
        objective: str,
        subtasks: list[dict[str, Any]],
    ) -> TeamRun:
        team = self.get_team(db, team_id)
        if not team.enabled:
            raise TeamError("Team is disabled.")
        objective = (objective or "").strip()
        if not objective:
            raise TeamError("Objective is required.")
        members = set(self.member_ids(team))
        # Only enabled members may receive work
        for mid in list(members):
            t = db.get(Teammate, mid)
            if not t or not t.enabled:
                members.discard(mid)
        if team.lead_teammate_id not in members:
            raise TeamError("Lead teammate is not available.")
        limits = PlanLimits(
            max_subtasks=min(team.max_subtasks, self.settings.team_max_subtasks),
            max_depth=min(team.max_depth, self.settings.team_max_depth),
            max_total_runs=min(team.max_total_runs, self.settings.team_max_total_runs),
            max_concurrent=min(team.max_concurrent, self.settings.team_max_concurrent),
            max_retries=min(team.max_retries, self.settings.team_max_retries),
            allowed_member_ids=frozenset(members),
            lead_teammate_id=team.lead_teammate_id,
        )
        try:
            plan = validate_plan(subtasks, limits)
        except PlanValidationError as exc:
            raise TeamError(exc.reason) from exc

        root = Path(self.settings.team_shared_artifacts_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        artifact_dir = root / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        run = TeamRun(
            id=run_id,
            team_id=team.id,
            lead_teammate_id=team.lead_teammate_id,
            objective=objective,
            status="pending",
            plan_json=json.dumps({"subtasks": plan}, ensure_ascii=False),
            shared_artifact_dir=str(artifact_dir),
        )
        db.add(run)
        db.flush()
        for step in plan:
            db.add(
                TeamSubtask(
                    id=str(uuid.uuid4()),
                    team_run_id=run.id,
                    subtask_key=step["subtask_key"],
                    assignee_teammate_id=step["assignee_teammate_id"],
                    objective=step["objective"],
                    output_format=step["output_format"],
                    depends_on_json=json.dumps(step["depends_on"]),
                    status="pending",
                    depth=step["depth"],
                    critical=step["critical"],
                    allowed_tool_categories_json=json.dumps(step["allowed_tool_categories"]),
                )
            )
        self._event(db, run.id, None, "plan_created", {"subtask_count": len(plan)})
        db.commit()
        db.refresh(run)
        return run

    def get_run(self, db: Session, run_id: str) -> TeamRun:
        run = db.get(TeamRun, run_id)
        if not run:
            raise TeamError("Team run not found.")
        return run

    def list_runs(self, db: Session, team_id: str | None = None) -> list[TeamRun]:
        q = db.query(TeamRun)
        if team_id:
            q = q.filter(TeamRun.team_id == team_id)
        return q.order_by(TeamRun.created_at.desc()).limit(50).all()

    def subtasks(self, db: Session, run_id: str) -> list[TeamSubtask]:
        return (
            db.query(TeamSubtask)
            .filter(TeamSubtask.team_run_id == run_id)
            .order_by(TeamSubtask.created_at)
            .all()
        )

    def events(self, db: Session, run_id: str) -> list[TeamEvent]:
        return (
            db.query(TeamEvent)
            .filter(TeamEvent.team_run_id == run_id)
            .order_by(TeamEvent.created_at)
            .all()
        )

    def _event(
        self,
        db: Session,
        run_id: str,
        subtask_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        db.add(
            TeamEvent(
                id=str(uuid.uuid4()),
                team_run_id=run_id,
                subtask_id=subtask_id,
                event_type=event_type,
                payload_json=json.dumps(payload or {}, ensure_ascii=False),
            )
        )

    def cancel_run(self, db: Session, run_id: str) -> TeamRun:
        run = self.get_run(db, run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        run.cancelled = True
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        for step in self.subtasks(db, run_id):
            if step.status in {"pending", "ready", "running", "waiting_approval"}:
                step.status = "cancelled"
                step.finished_at = datetime.now(timezone.utc)
        self._event(db, run_id, None, "run_cancelled", {})
        db.commit()
        db.refresh(run)
        return run

    def public_team(self, team: Team) -> dict[str, Any]:
        return {
            "id": team.id,
            "name": team.name,
            "description": team.description,
            "lead_teammate_id": team.lead_teammate_id,
            "member_ids": self.member_ids(team),
            "enabled": team.enabled,
            "max_subtasks": team.max_subtasks,
            "max_concurrent": team.max_concurrent,
            "max_depth": team.max_depth,
            "max_retries": team.max_retries,
            "max_total_runs": team.max_total_runs,
            "created_at": team.created_at,
            "updated_at": team.updated_at,
        }

    def public_run(self, db: Session, run: TeamRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "team_id": run.team_id,
            "lead_teammate_id": run.lead_teammate_id,
            "objective": run.objective,
            "status": run.status,
            "cancelled": run.cancelled,
            "plan": json.loads(run.plan_json or "{}"),
            "report": json.loads(run.report_json or "{}"),
            "shared_artifact_dir": run.shared_artifact_dir,
            "error_message": run.error_message,
            "subtasks": [self.public_subtask(s) for s in self.subtasks(db, run.id)],
            "events": [
                {
                    "id": e.id,
                    "subtask_id": e.subtask_id,
                    "event_type": e.event_type,
                    "payload": json.loads(e.payload_json or "{}"),
                    "created_at": e.created_at,
                }
                for e in self.events(db, run.id)
            ],
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "finished_at": run.finished_at,
        }

    @staticmethod
    def public_subtask(s: TeamSubtask) -> dict[str, Any]:
        return {
            "id": s.id,
            "subtask_key": s.subtask_key,
            "assignee_teammate_id": s.assignee_teammate_id,
            "objective": s.objective,
            "output_format": s.output_format,
            "depends_on": json.loads(s.depends_on_json or "[]"),
            "status": s.status,
            "attempts": s.attempts,
            "depth": s.depth,
            "critical": s.critical,
            "allowed_tool_categories": json.loads(s.allowed_tool_categories_json or "[]"),
            "result": json.loads(s.result_json or "{}"),
            "artifact_refs": json.loads(s.artifact_refs_json or "[]"),
            "conversation_id": s.conversation_id,
            "agent_run_id": s.agent_run_id,
            "error_message": s.error_message,
            "checkpoint": json.loads(s.checkpoint_json or "{}"),
            "started_at": s.started_at,
            "finished_at": s.finished_at,
        }
