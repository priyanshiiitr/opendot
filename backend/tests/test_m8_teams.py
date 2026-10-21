"""Milestone 8: multi-agent teams, plan validation, delegation, permissions."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.manager import ComputerManager
from app.config import Settings, get_settings
from app.database import Teammate, init_db
from app.teams.coordinator import TeamCoordinator
from app.teams.service import TeamError, TeamService
from app.teams.validation import PlanLimits, PlanValidationError, validate_plan


@pytest.fixture
def m8_env(tmp_path, monkeypatch):
    db_path = tmp_path / "m8.db"
    root = tmp_path / "computers"
    artifacts = tmp_path / "team_artifacts"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_TEAM_SHARED_ARTIFACTS_ROOT", str(artifacts))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    get_settings.cache_clear()

    from app import database as database_mod
    from app import main as main_mod

    database_mod.engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    main_mod.SessionLocal = database_mod.SessionLocal
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        team_shared_artifacts_root=str(artifacts),
        automation_worker_enabled=False,
        model_provider="mock",
    )
    policy = NavigationPolicy(fixture_mode=False)
    mgr = ComputerManager(settings, database_mod.SessionLocal, policy)
    main_mod.computer_manager = mgr
    main_mod.settings = settings
    main_mod.team_service = TeamService(settings)
    main_mod.team_coordinator = TeamCoordinator(
        settings, database_mod.SessionLocal, main_mod.runtime, main_mod.team_service
    )
    # Runtime still points at old SessionLocal for computers — rebind runtime's computer manager
    main_mod.runtime.computer_manager = mgr
    main_mod.runtime.settings = settings
    init_db()
    yield {
        "SessionLocal": database_mod.SessionLocal,
        "settings": settings,
        "service": main_mod.team_service,
        "coordinator": main_mod.team_coordinator,
        "artifacts": artifacts,
    }
    get_settings.cache_clear()


def _seed_team(db, service: TeamService):
    lead = Teammate(name="Lead", description="chief", system_prompt="Lead", role_label="chief", enabled=True)
    research = Teammate(name="Research", description="research", system_prompt="Researcher", role_label="research", enabled=True)
    report = Teammate(name="Report", description="report", system_prompt="Reporter", role_label="report", enabled=True)
    disabled = Teammate(name="Off", description="off", system_prompt="x", enabled=False)
    db.add_all([lead, research, report, disabled])
    db.commit()
    for t in (lead, research, report, disabled):
        db.refresh(t)
    team = service.create_team(
        db,
        {
            "name": "Startup squad",
            "lead_teammate_id": lead.id,
            "member_ids": [lead.id, research.id, report.id],
            "max_concurrent": 1,
            "max_depth": 1,
            "max_subtasks": 8,
        },
    )
    return lead, research, report, disabled, team


def test_plan_rejects_cycles_and_unknown_deps():
    limits = PlanLimits(allowed_member_ids=frozenset({1, 2}), max_subtasks=5)
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(
            [
                {"subtask_key": "a", "assignee_teammate_id": 1, "objective": "A", "depends_on": ["b"]},
                {"subtask_key": "b", "assignee_teammate_id": 2, "objective": "B", "depends_on": ["a"]},
            ],
            limits,
        )
    with pytest.raises(PlanValidationError, match="Unknown dependency"):
        validate_plan(
            [{"subtask_key": "a", "assignee_teammate_id": 1, "objective": "A", "depends_on": ["missing"]}],
            limits,
        )
    with pytest.raises(PlanValidationError, match="max_subtasks"):
        validate_plan(
            [
                {"subtask_key": f"s{i}", "assignee_teammate_id": 1, "objective": "x"}
                for i in range(6)
            ],
            limits,
        )
    with pytest.raises(PlanValidationError, match="not an authorized"):
        validate_plan(
            [{"subtask_key": "a", "assignee_teammate_id": 99, "objective": "A"}],
            limits,
        )
    with pytest.raises(PlanValidationError, match="max_depth"):
        validate_plan(
            [{"subtask_key": "a", "assignee_teammate_id": 1, "objective": "A", "depth": 2}],
            PlanLimits(allowed_member_ids=frozenset({1}), max_depth=1),
        )


def test_disabled_assignee_cannot_receive_work(m8_env):
    SessionLocal = m8_env["SessionLocal"]
    service: TeamService = m8_env["service"]
    with SessionLocal() as db:
        lead, research, report, disabled, team = _seed_team(db, service)
        with pytest.raises(TeamError, match="not an authorized|disabled|available"):
            # disabled not in members — reject unknown assignee
            service.create_run(
                db,
                team_id=team.id,
                objective="Do work",
                subtasks=[
                    {
                        "subtask_key": "x",
                        "assignee_teammate_id": disabled.id,
                        "objective": "Nope",
                    }
                ],
            )


def test_sequential_delegation_and_final_report(m8_env):
    SessionLocal = m8_env["SessionLocal"]
    service: TeamService = m8_env["service"]
    coordinator: TeamCoordinator = m8_env["coordinator"]
    with SessionLocal() as db:
        lead, research, report, _disabled, team = _seed_team(db, service)
        run = service.create_run(
            db,
            team_id=team.id,
            objective="Research then report on AI startups",
            subtasks=[
                {
                    "subtask_key": "research",
                    "assignee_teammate_id": research.id,
                    "objective": "List two AI startup names for a shortlist.",
                    "depends_on": [],
                    "allowed_tool_categories": ["local", "workspace"],
                },
                {
                    "subtask_key": "report",
                    "assignee_teammate_id": report.id,
                    "objective": "Write a short ranked shortlist from prior research results.",
                    "depends_on": ["research"],
                    "allowed_tool_categories": ["local", "workspace"],
                },
            ],
        )
        run_id = run.id

    out = asyncio.run(coordinator.tick(run_id))
    assert out["status"] == "completed"
    keys = {s["subtask_key"]: s for s in out["subtasks"]}
    assert keys["research"]["status"] == "completed"
    assert keys["report"]["status"] == "completed"
    assert keys["research"]["conversation_id"]
    assert keys["report"]["result"]["findings"]
    # Report must see dependency context was available (artifact exists)
    assert out["report"]["honest_status"] == "completed"
    assert out["report"]["final_report"]
    events = {e["event_type"] for e in out["events"]}
    assert "plan_created" in events
    assert "result_submitted" in events
    assert "final_review_completed" in events


def test_dependency_blocks_on_failure(m8_env):
    SessionLocal = m8_env["SessionLocal"]
    service: TeamService = m8_env["service"]
    coordinator: TeamCoordinator = m8_env["coordinator"]
    with SessionLocal() as db:
        lead, research, report, _d, team = _seed_team(db, service)
        # Force research assignee disabled mid-flight by removing from enabled after plan
        run = service.create_run(
            db,
            team_id=team.id,
            objective="Will fail research",
            subtasks=[
                {
                    "subtask_key": "research",
                    "assignee_teammate_id": research.id,
                    "objective": "Research",
                    "critical": True,
                },
                {
                    "subtask_key": "report",
                    "assignee_teammate_id": report.id,
                    "objective": "Report",
                    "depends_on": ["research"],
                    "critical": True,
                },
            ],
        )
        research.enabled = False
        db.commit()
        run_id = run.id

    out = asyncio.run(coordinator.tick(run_id))
    assert out["status"] == "failed"
    by_key = {s["subtask_key"]: s for s in out["subtasks"]}
    assert by_key["research"]["status"] == "failed"
    assert by_key["report"]["status"] == "failed"
    assert "prerequisite" in (by_key["report"]["error_message"] or "").lower()


def test_cancel_stops_pending(m8_env):
    SessionLocal = m8_env["SessionLocal"]
    service: TeamService = m8_env["service"]
    with SessionLocal() as db:
        lead, research, report, _d, team = _seed_team(db, service)
        run = service.create_run(
            db,
            team_id=team.id,
            objective="Cancel me",
            subtasks=[
                {"subtask_key": "a", "assignee_teammate_id": research.id, "objective": "A"},
                {"subtask_key": "b", "assignee_teammate_id": report.id, "objective": "B", "depends_on": ["a"]},
            ],
        )
        cancelled = service.cancel_run(db, run.id)
        assert cancelled.status == "cancelled"
        assert all(s.status == "cancelled" for s in service.subtasks(db, run.id))


def test_permission_categories_do_not_expand_via_plan(m8_env):
    """Plan may request categories but broker still denies unknown tools; telegram not auto-granted."""
    from app.permissions import PermissionBroker, AuthContext
    from app.tools import ToolSpec
    from pydantic import BaseModel

    class Empty(BaseModel):
        pass

    async def _noop(_a):
        return {}

    tool = ToolSpec("telegram_send_message", "send", Empty, "approval_required", 5, _noop)
    # Skill categories without telegram → denied
    auth = AuthContext(skill_allowed_categories=["local", "workspace"])
    assert PermissionBroker().assess(tool, auth).status == "denied"


def test_m8_api_team_flow(m8_env):
    from app import main as main_mod

    with TestClient(main_mod.app) as client:
        # Create specialists
        lead = client.post(
            "/api/teammates",
            json={"name": "Chief", "description": "lead", "role_label": "chief", "system_prompt": "Lead"},
        ).json()
        r = client.post(
            "/api/teammates",
            json={"name": "Scout", "description": "research", "role_label": "research", "system_prompt": "Research"},
        ).json()
        team = client.post(
            "/api/teams",
            json={
                "name": "API Team",
                "lead_teammate_id": lead["id"],
                "member_ids": [lead["id"], r["id"]],
                "max_concurrent": 1,
            },
        ).json()
        assert team["lead_teammate_id"] == lead["id"]
        bad = client.post(
            "/api/teams/" + team["id"] + "/runs",
            json={
                "objective": "Bad plan",
                "subtasks": [
                    {
                        "subtask_key": "a",
                        "assignee_teammate_id": 99999,
                        "objective": "nope",
                    }
                ],
            },
        )
        assert bad.status_code == 400
        run = client.post(
            f"/api/teams/{team['id']}/runs",
            json={
                "objective": "Quick research note",
                "subtasks": [
                    {
                        "subtask_key": "research",
                        "assignee_teammate_id": r["id"],
                        "objective": "Summarize why AI agents matter in one sentence.",
                    }
                ],
            },
        ).json()
        assert run["status"] == "pending"
        done = client.post(f"/api/team-runs/{run['id']}/tick").json()
        assert done["status"] == "completed"
        assert done["subtasks"][0]["status"] == "completed"
        assert done["report"]["final_report"]
