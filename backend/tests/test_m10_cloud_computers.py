"""Milestone 10: computer provider contract, isolation, lifecycle (no billable cloud)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.billable_stub import BillableCloudNotConfigured, BillableCloudStub
from app.computers.manager import ComputerError, ComputerManager
from app.computers.mock_cloud import MockCloudComputer
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db
from app.main import app


@pytest.fixture
def m10_env(tmp_path, monkeypatch):
    db_path = tmp_path / "m10.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE", "false")
    get_settings.cache_clear()
    from app import database as database_mod
    from app import main as main_mod

    database_mod.engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    main_mod.SessionLocal = database_mod.SessionLocal
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        automation_worker_enabled=False,
        model_provider="mock",
        computer_allow_billable=False,
    )
    policy = NavigationPolicy(fixture_mode=False)
    mgr = ComputerManager(settings, database_mod.SessionLocal, policy)
    main_mod.computer_manager = mgr
    init_db()
    yield {"root": root, "manager": mgr, "SessionLocal": database_mod.SessionLocal, "settings": settings}
    get_settings.cache_clear()


def test_mock_provider_idempotent_provision_and_delete(tmp_path):
    root = tmp_path / "mock-a"
    a = MockCloudComputer(teammate_id=1, root=root)
    first = asyncio.run(a.provision())
    second = asyncio.run(a.provision())
    assert first["computer_id"] == second["computer_id"]
    assert second.get("idempotent") is True
    assert a.provision_count == 2
    asyncio.run(a.start())
    assert (asyncio.run(a.health()))["healthy"] is True
    preview = asyncio.run(a.preview())
    assert preview[:8] == b"\x89PNG\r\n\x1a\n"
    asyncio.run(a.enter_human_control())
    st = asyncio.run(a.status())
    assert st["control_state"] == "human_control"
    asyncio.run(a.release_human_control())
    with pytest.raises(RuntimeError, match="confirm"):
        asyncio.run(a.delete(confirm=False))
    asyncio.run(a.delete(confirm=True))
    assert a.deleted is True
    assert (root / "DELETED").is_file()


def test_mock_isolation_separate_roots(tmp_path):
    a = MockCloudComputer(teammate_id=1, root=tmp_path / "a")
    b = MockCloudComputer(teammate_id=2, root=tmp_path / "b")
    asyncio.run(a.provision())
    asyncio.run(b.provision())
    assert a.computer_id != b.computer_id
    assert Path(a.workspace_paths()["workspace"]) != Path(b.workspace_paths()["workspace"])


def test_billable_stub_refuses_provision():
    stub = BillableCloudStub("fly")
    with pytest.raises(BillableCloudNotConfigured):
        asyncio.run(stub.provision())


def test_manager_provision_isolated_linux_dev(m10_env):
    mgr: ComputerManager = m10_env["manager"]
    SessionLocal = m10_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        # Remove auto-created local computer so we can provision isolated provider.
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()

    status = asyncio.run(mgr.provision(tid, provider="isolated_linux_dev"))
    assert status["provider"] == "isolated_linux_dev"
    assert status["isolation_tier"] == "filesystem_sandbox"
    again = asyncio.run(mgr.provision(tid, provider="isolated_linux_dev"))
    assert again["id"] == status["id"]
    root = Path(status["workspace_path"]).parent
    assert (root / "metadata" / "isolation.json").is_file()

    health = asyncio.run(mgr.health(tid))
    assert health["health"]["root_ok"] is True

    with pytest.raises(ComputerError, match="already exists|different provider|Delete"):
        asyncio.run(mgr.provision(tid, provider="local_browser"))


def test_manager_delete_requires_confirm_and_removes_row(m10_env):
    mgr: ComputerManager = m10_env["manager"]
    SessionLocal = m10_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        rec = mgr.get_or_create_record(db, tid)
        computer_id = rec.id
        root = Path(rec.root_path)
    (root / "workspace" / "keep.md").write_text("x", encoding="utf-8")
    with pytest.raises(ComputerError, match="confirm"):
        asyncio.run(mgr.delete_computer(tid, confirm=False))
    result = asyncio.run(mgr.delete_computer(tid, confirm=True))
    assert result["deleted"] is True
    assert result["computer_id"] == computer_id
    with SessionLocal() as db:
        assert db.get(ComputerRecord, computer_id) is None
    assert not root.exists()


def test_cross_teammate_assert_owner(m10_env):
    mgr: ComputerManager = m10_env["manager"]
    SessionLocal = m10_env["SessionLocal"]
    with SessionLocal() as db:
        a = db.query(Teammate).first()
        b = Teammate(name="Other", description="", system_prompt="x")
        db.add(b)
        db.commit()
        db.refresh(b)
        ca = mgr.get_or_create_record(db, a.id)
        cb = mgr.get_or_create_record(db, b.id)
        with pytest.raises(ComputerError):
            mgr.assert_owner(db, ca.id, b.id)
        assert mgr.assert_owner(db, cb.id, b.id).id == cb.id


def test_billable_provider_blocked_by_settings(m10_env):
    mgr: ComputerManager = m10_env["manager"]
    SessionLocal = m10_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()
    with pytest.raises(ComputerError, match="disabled|not auto-provisioned|Billable"):
        asyncio.run(mgr.provision(tid, provider="billable_cloud"))


def test_api_provision_health_delete(m10_env):
    from app import main as main_mod

    main_mod.computer_manager = m10_env["manager"]
    with TestClient(app) as client:
        created = client.post("/api/teammates", json={"name": "CloudDev", "description": "iso"})
        assert created.status_code == 200
        tid = created.json()["id"]
        # Delete auto local computer via API so we can re-provision isolated
        deleted = client.post(f"/api/teammates/{tid}/computer/delete?confirm=true")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        prov = client.post(
            f"/api/teammates/{tid}/computer/provision",
            json={"provider": "isolated_linux_dev"},
        )
        assert prov.status_code == 200
        body = prov.json()
        assert body["provider"] == "isolated_linux_dev"
        health = client.get(f"/api/teammates/{tid}/computer/health")
        assert health.status_code == 200
        assert "health" in health.json()
        no_confirm = client.post(f"/api/teammates/{tid}/computer/delete?confirm=false")
        assert no_confirm.status_code == 400


def test_takeover_control_state_machine(m10_env):
    mgr: ComputerManager = m10_env["manager"]
    SessionLocal = m10_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id

    async def scenario():
        await mgr.start(tid, headed=False)
        taken = await mgr.takeover(tid)
        assert taken["control_state"] == "human_control"
        assert taken["state"] == "human_control"
        released = await mgr.release_control(tid)
        assert released["control_state"] == "agent_control"
        await mgr.stop(tid)

    asyncio.run(scenario())
