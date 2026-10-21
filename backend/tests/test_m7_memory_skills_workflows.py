"""Milestone 7: memory, skills, grants, multi-step workflows (no paid APIs)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.manager import ComputerManager
from app.config import Settings, get_settings
from app.database import ConnectorGrant, Teammate, init_db
from app.memory.service import MemoryError, MemoryService
from app.permissions import PermissionBroker, TOOL_CATEGORIES, load_auth_context
from app.skills.loader import SkillError, load_skill_dir
from app.skills.registry import SkillRegistry
from app.tools import ToolRegistry, ToolSpec
from app.workflows.service import WorkflowService


@pytest.fixture
def m7_env(tmp_path, monkeypatch):
    db_path = tmp_path / "m7.db"
    root = tmp_path / "computers"
    skills = tmp_path / "skills"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_SKILLS_ROOT_DIR", str(skills))
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
        skills_root_dir=str(skills),
        automation_worker_enabled=False,
        model_provider="mock",
    )
    policy = NavigationPolicy(fixture_mode=False)
    mgr = ComputerManager(settings, database_mod.SessionLocal, policy)
    main_mod.computer_manager = mgr
    main_mod.settings = settings
    main_mod.skill_registry = SkillRegistry(skills)
    # Seed builtins into tmp skills root
    main_mod.skill_registry.ensure_builtin_seeded()
    init_db()
    yield {
        "root": root,
        "skills": skills,
        "SessionLocal": database_mod.SessionLocal,
        "manager": mgr,
        "settings": settings,
    }
    get_settings.cache_clear()


def test_memory_crud_scope_secrets_and_deletion(m7_env):
    SessionLocal = m7_env["SessionLocal"]
    svc = MemoryService()
    with SessionLocal() as db:
        a = svc.create(
            db,
            content="Prefer concise research reports with source links.",
            memory_type="preference",
            scope="user",
            source="explicit",
        )
        b = svc.create(
            db,
            content="Teammate Atlas likes CSV exports.",
            memory_type="teammate",
            scope="teammate",
            scope_id="1",
            source="explicit",
        )
        svc.create(
            db,
            content="Other teammate private note.",
            memory_type="teammate",
            scope="teammate",
            scope_id="2",
            source="explicit",
        )
        with pytest.raises(MemoryError):
            svc.create(db, content="api_key=gsk_secret_value_here", memory_type="fact", scope="user")
        listed = svc.list(db, scope="teammate", scope_id="1")
        assert len(listed) == 1 and listed[0].id == b.id
        retrieved = svc.retrieve_for_task(db, teammate_id=1, query="research report sources")
        ids = {r.id for r in retrieved}
        assert a.id in ids
        assert b.id in ids
        assert all(r.scope_id != "2" or r.scope != "teammate" for r in retrieved)
        # Correction supersedes
        new = svc.update(db, a.id, "Prefer concise research reports with source links and a short summary.")
        assert new.supersedes_id == a.id
        old = db.get(type(a), a.id)
        assert old.status == "superseded"
        svc.delete(db, b.id)
        assert svc.list(db, scope="teammate", scope_id="1") == []
        with pytest.raises(MemoryError):
            svc.get(db, b.id)


def test_memory_context_budget(m7_env):
    SessionLocal = m7_env["SessionLocal"]
    svc = MemoryService()
    with SessionLocal() as db:
        for i in range(20):
            svc.create(db, content=f"Preference number {i} about reports", memory_type="preference", scope="user")
        rows = svc.retrieve_for_task(db, teammate_id=1, query="reports", limit=5)
        assert len(rows) == 5
        block, ids = svc.format_for_prompt(rows)
        assert "Authorized persistent memories" in block
        assert len(ids) == 5


def test_skill_loader_rejects_traversal_and_disabled(m7_env):
    skills: Path = m7_env["skills"]
    pkg = load_skill_dir(skills / "startup-research")
    assert pkg.skill_id == "startup-research"
    assert pkg.version == "1.0.0"
    # Symlink rejection
    bad = skills / "evil-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nid: evil-skill\nversion: 0.0.1\nname: Evil\n---\n# x\n", encoding="utf-8")
    target = bad / "escape.txt"
    target.write_text("nope", encoding="utf-8")
    link = bad / "link.md"
    try:
        link.symlink_to(target)
        with pytest.raises(SkillError):
            load_skill_dir(bad)
    except OSError:
        pytest.skip("symlinks not available on this platform")

    SessionLocal = m7_env["SessionLocal"]
    reg = SkillRegistry(skills)
    with SessionLocal() as db:
        row = reg.install(db, pkg, reviewed=False, enabled=True)
        assert row.enabled is False  # not reviewed
        with pytest.raises(SkillError):
            reg.set_enabled(db, row.id, True)
        reg.mark_reviewed(db, row.id)
        reg.set_enabled(db, row.id, True)
        with pytest.raises(SkillError):
            reg.resolve_enabled(db, "website-audit", None)


def test_permission_grant_and_skill_category_gate(m7_env):
    SessionLocal = m7_env["SessionLocal"]
    broker = PermissionBroker()
    registry = ToolRegistry("UTC", 5, 2000, 2000)

    async def _noop(_args):
        return {}

    from pydantic import BaseModel

    class Empty(BaseModel):
        pass

    registry.register(
        ToolSpec("telegram_send_message", "send", Empty, "approval_required", 5, _noop)
    )
    tool = registry.get("telegram_send_message")
    with SessionLocal() as db:
        t = Teammate(name="G", description="", system_prompt="x")
        db.add(t)
        db.commit()
        db.refresh(t)
        # No grant → legacy path still approval_required
        auth = load_auth_context(db, teammate_id=t.id, connector_id="telegram")
        assert auth.connector_grant_present is False
        assert broker.assess(tool, auth).status == "approval_required"
        # Explicit grant without send → denied
        g = ConnectorGrant(
            id="g1",
            teammate_id=t.id,
            connector_id="telegram",
            operations_json='["telegram_get_bot_info"]',
            resource_scope_json="{}",
            enabled=True,
        )
        db.add(g)
        db.commit()
        auth2 = load_auth_context(db, teammate_id=t.id, connector_id="telegram")
        assert auth2.connector_grant_present is True
        assert broker.assess(tool, auth2).status == "denied"
        # Skill category restriction
        auth3 = load_auth_context(
            db, teammate_id=t.id, connector_id=None, skill_allowed_categories=["local"]
        )
        assert broker.assess(tool, auth3).status == "denied"
        assert TOOL_CATEGORIES["memory_save"] == "memory"


def test_workflow_plan_resume_and_verify(m7_env):
    SessionLocal = m7_env["SessionLocal"]
    skills: Path = m7_env["skills"]
    mgr: ComputerManager = m7_env["manager"]
    reg = SkillRegistry(skills)
    wf_svc = WorkflowService()
    mem = MemoryService()
    with SessionLocal() as db:
        t = Teammate(name="W", description="", system_prompt="x")
        db.add(t)
        db.commit()
        db.refresh(t)
        mem.create(
            db,
            content="Prefer concise research reports with source links.",
            memory_type="preference",
            scope="user",
        )
        pkg = load_skill_dir(skills / "startup-research")
        install = reg.install(db, pkg, teammate_id=t.id, reviewed=True, enabled=True)
        assert install.enabled
        memories = mem.retrieve_for_task(db, teammate_id=t.id, query="startup research")
        steps = wf_svc.plan_from_skill(pkg, "Research AI startups")
        run = wf_svc.create(
            db,
            teammate_id=t.id,
            title="Startup shortlist",
            instruction="Research AI startups",
            steps=steps,
            skill_id=pkg.skill_id,
            skill_version=pkg.version,
            memory_ids=[m.id for m in memories],
        )
        store = mgr.workspace_for_teammate(t.id)
        # Advance load_context
        out1 = wf_svc.advance_deterministic(db, run.id, workspace_root=store.workspace, skill=pkg)
        assert any(s["step_key"] == "load_context" and s["status"] == "completed" for s in out1["steps"])
        # Execute not done yet
        out2 = wf_svc.advance_deterministic(db, run.id, workspace_root=store.workspace, skill=pkg)
        assert out2.get("awaiting") == "execute"
        # Persist artifact then complete execute
        store.write_text(
            "startup-research-report.md",
            "# Report\n\n## Sources\n- https://example.com\n",
            area="workspace",
        )
        out3 = wf_svc.advance_deterministic(
            db, run.id, workspace_root=store.workspace, skill=pkg, complete_execute=True
        )
        assert any(s["step_key"] == "execute" and s["status"] == "completed" for s in out3["steps"])
        # Verify
        out4 = wf_svc.advance_deterministic(db, run.id, workspace_root=store.workspace, skill=pkg)
        assert out4["status"] == "completed"
        assert out4["report"]["verification"]["passed"] is True
        assert out4["skill_version"] == "1.0.0"
        assert out4["memory_ids"]


def test_workflow_fails_without_artifact(m7_env):
    SessionLocal = m7_env["SessionLocal"]
    skills: Path = m7_env["skills"]
    mgr: ComputerManager = m7_env["manager"]
    wf_svc = WorkflowService()
    with SessionLocal() as db:
        t = Teammate(name="F", description="", system_prompt="x")
        db.add(t)
        db.commit()
        db.refresh(t)
        pkg = load_skill_dir(skills / "website-audit")
        steps = wf_svc.plan_from_skill(pkg, "audit")
        run = wf_svc.create(
            db,
            teammate_id=t.id,
            title="Audit",
            instruction="audit",
            steps=steps,
            skill_id=pkg.skill_id,
            skill_version=pkg.version,
        )
        store = mgr.workspace_for_teammate(t.id)
        wf_svc.advance_deterministic(db, run.id, workspace_root=store.workspace, skill=pkg)
        wf_svc.advance_deterministic(
            db, run.id, workspace_root=store.workspace, skill=pkg, complete_execute=True
        )
        out = wf_svc.advance_deterministic(db, run.id, workspace_root=store.workspace, skill=pkg)
        assert out["status"] == "failed"


def test_m7_api_memory_skills_workflows(m7_env):
    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        teammate_id = client.get("/api/teammates").json()[0]["id"]
        mem = client.post(
            "/api/memories",
            json={
                "content": "Prefer concise research reports with source links.",
                "memory_type": "preference",
                "scope": "user",
                "source": "explicit",
            },
        ).json()
        assert mem["status"] == "active"
        listed = client.get("/api/memories?scope=user").json()
        assert any(m["id"] == mem["id"] for m in listed)
        # Reject secrets
        bad = client.post("/api/memories", json={"content": "password=hunter2", "memory_type": "fact"})
        assert bad.status_code == 400
        catalog = client.get("/api/skills/catalog").json()
        assert any(s["skill_id"] == "startup-research" for s in catalog)
        inst = client.post(
            "/api/skills/installs",
            json={"skill_id": "startup-research", "teammate_id": teammate_id, "reviewed": True, "enabled": True},
        ).json()
        assert inst["enabled"] is True
        assert inst["reviewed"] is True
        grant = client.post(
            "/api/connector-grants",
            json={
                "teammate_id": teammate_id,
                "connector_id": "telegram",
                "operations": ["telegram_get_bot_info"],
                "enabled": True,
            },
        ).json()
        assert grant["operations"] == ["telegram_get_bot_info"]
        wf = client.post(
            "/api/workflows",
            json={
                "teammate_id": teammate_id,
                "title": "Research",
                "instruction": "startup research with sources",
                "skill_id": "startup-research",
            },
        ).json()
        assert wf["skill_version"] == "1.0.0"
        assert mem["id"] in wf["memory_ids"]
        a1 = client.post(f"/api/workflows/{wf['id']}/advance").json()
        assert any(s["status"] == "completed" for s in a1["steps"] if s["step_key"] == "load_context")
        # Write artifact via workspace API path through computer manager
        from app import main as main_mod

        store = main_mod.computer_manager.workspace_for_teammate(teammate_id)
        store.write_text(
            "startup-research-report.md",
            "# R\n\n## Sources\n- https://example.com\n",
            area="workspace",
        )
        client.post(f"/api/workflows/{wf['id']}/advance?complete_execute=true")
        done = client.post(f"/api/workflows/{wf['id']}/advance").json()
        assert done["status"] == "completed"
        # Soft delete memory
        deleted = client.delete(f"/api/memories/{mem['id']}").json()
        assert deleted["status"] == "deleted"
        assert client.get("/api/memories?scope=user").json() == []
        # Revoke grant
        assert client.delete(f"/api/connector-grants/{grant['id']}").json()["revoked"] is True
