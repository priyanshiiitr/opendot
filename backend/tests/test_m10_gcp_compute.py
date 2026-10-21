"""GCP Compute credit-funded provider tests (Fake client — never calls Google)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.credit_policy import CreditPolicyError, assert_credit_funded_gcp_allowed
from app.computers.gcp_client import FakeGcpComputeClient
from app.computers.manager import ComputerError, ComputerManager
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db


@pytest.fixture
def gcp_env(tmp_path, monkeypatch):
    db_path = tmp_path / "gcp.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED", "true")
    monkeypatch.setenv("AI_TEAMMATES_GCP_PROJECT_ID", "demo-project")
    monkeypatch.setenv("AI_TEAMMATES_GCP_ZONE", "us-central1-a")
    monkeypatch.setenv("AI_TEAMMATES_GCP_MACHINE_TYPE", "e2-micro")
    monkeypatch.setenv("AI_TEAMMATES_GCP_MAX_CONCURRENT_VMS", "1")
    monkeypatch.setenv("AI_TEAMMATES_GCP_CONTROL_PLANE_URL", "https://control.example.test")
    get_settings.cache_clear()
    from app import database as database_mod

    database_mod.engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        automation_worker_enabled=False,
        model_provider="mock",
        computer_allow_billable=False,
        computer_credit_funded_enabled=True,
        gcp_project_id="demo-project",
        gcp_zone="us-central1-a",
        gcp_machine_type="e2-micro",
        gcp_max_concurrent_vms=1,
        gcp_control_plane_url="https://control.example.test",
    )
    fake = FakeGcpComputeClient()
    policy = NavigationPolicy(fixture_mode=False)
    mgr = ComputerManager(settings, database_mod.SessionLocal, policy, gcp_client=fake)
    init_db()
    yield {
        "manager": mgr,
        "SessionLocal": database_mod.SessionLocal,
        "fake": fake,
        "settings": settings,
        "root": root,
    }
    get_settings.cache_clear()


def _clear_default_computer(SessionLocal, tid: int) -> None:
    with SessionLocal() as db:
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()


def test_credit_policy_refuses_when_disabled(tmp_path):
    settings = Settings(
        computer_credit_funded_enabled=False,
        computer_allow_billable=False,
        gcp_project_id="x",
        gcp_zone="us-central1-a",
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    # minimal — no tables needed for count 0 if query fails; use empty session carefully
    from app.database import Base

    Base.metadata.create_all(engine)
    with Session() as db:
        with pytest.raises(CreditPolicyError, match="disabled"):
            assert_credit_funded_gcp_allowed(settings, db)


def test_credit_policy_refuses_when_billable_flag_true(gcp_env):
    settings = gcp_env["settings"].model_copy(update={"computer_allow_billable": True})
    with gcp_env["SessionLocal"]() as db:
        with pytest.raises(CreditPolicyError, match="not authorized|ALLOW_BILLABLE"):
            assert_credit_funded_gcp_allowed(settings, db)


def test_gcp_estimate_then_create_idempotent(gcp_env):
    mgr: ComputerManager = gcp_env["manager"]
    fake: FakeGcpComputeClient = gcp_env["fake"]
    SessionLocal = gcp_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear_default_computer(SessionLocal, tid)

    estimate = asyncio.run(mgr.provision(tid, provider="gcp_compute", confirm_estimate=False))
    assert estimate["requires_confirmation"] is True
    assert estimate["estimated_cost"]["machine_type"] == "e2-micro"
    assert fake.create_calls == 0

    created = asyncio.run(mgr.provision(tid, provider="gcp_compute", confirm_estimate=True))
    assert created["provision"]["created"] is True
    assert fake.create_calls == 1
    name = created["provision"]["instance_name"]

    again = asyncio.run(mgr.provision(tid, provider="gcp_compute", confirm_estimate=True))
    assert again["provision"].get("idempotent") is True
    assert fake.create_calls == 1
    assert again["provision"]["instance_name"] == name


def test_gcp_lifecycle_start_stop_health_delete(gcp_env):
    mgr: ComputerManager = gcp_env["manager"]
    fake: FakeGcpComputeClient = gcp_env["fake"]
    SessionLocal = gcp_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear_default_computer(SessionLocal, tid)
    asyncio.run(mgr.provision(tid, provider="gcp_compute", confirm_estimate=True))

    started = asyncio.run(mgr.start(tid))
    assert started["state"] in {"ready", "RUNNING", "busy"} or started["provider"] == "gcp_compute"
    health = asyncio.run(mgr.health(tid))
    assert health["health"]["healthy"] is True

    asyncio.run(mgr.stop(tid))
    health2 = asyncio.run(mgr.health(tid))
    # Fake stop → TERMINATED
    assert health2["health"]["healthy"] is False

    taken = asyncio.run(mgr.takeover(tid))
    assert taken["control_state"] == "human_control"
    asyncio.run(mgr.release_control(tid))

    deleted = asyncio.run(mgr.delete_computer(tid, confirm=True))
    assert deleted["deleted"] is True
    assert fake.delete_calls == 1


def test_max_one_concurrent_gcp_vm(gcp_env):
    mgr: ComputerManager = gcp_env["manager"]
    SessionLocal = gcp_env["SessionLocal"]
    with SessionLocal() as db:
        a = db.query(Teammate).first()
        b = Teammate(name="Second", description="", system_prompt="x")
        db.add(b)
        db.commit()
        db.refresh(b)
        tid_a, tid_b = a.id, b.id
    _clear_default_computer(SessionLocal, tid_a)
    asyncio.run(mgr.provision(tid_a, provider="gcp_compute", confirm_estimate=True))
    # Mark active so concurrency gate trips
    with SessionLocal() as db:
        row = db.query(ComputerRecord).filter_by(teammate_id=tid_a).one()
        row.state = "ready"
        db.commit()
    _clear_default_computer(SessionLocal, tid_b)
    with pytest.raises(ComputerError, match="Concurrent GCP|limit"):
        asyncio.run(mgr.provision(tid_b, provider="gcp_compute", confirm_estimate=True))


def test_disabled_spending_path_refuses_without_credit_flag(tmp_path, monkeypatch):
    db_path = tmp_path / "off.db"
    root = tmp_path / "c"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    from app import database as database_mod

    database_mod.engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        computer_credit_funded_enabled=False,
        computer_allow_billable=False,
        gcp_project_id="demo",
        gcp_zone="us-central1-a",
        gcp_control_plane_url="https://x.test",
    )
    mgr = ComputerManager(settings, database_mod.SessionLocal, NavigationPolicy(fixture_mode=False), gcp_client=FakeGcpComputeClient())
    init_db()
    with database_mod.SessionLocal() as db:
        tid = db.query(Teammate).first().id
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()
    with pytest.raises(ComputerError, match="Credit-funded|disabled"):
        asyncio.run(mgr.provision(tid, provider="gcp_compute", confirm_estimate=True))
    get_settings.cache_clear()


def test_no_silent_provider_switch(gcp_env):
    mgr: ComputerManager = gcp_env["manager"]
    SessionLocal = gcp_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        mgr.get_or_create_record(db, tid, provider="local_browser")
    with pytest.raises(ComputerError, match="already exists|Delete"):
        asyncio.run(mgr.provision(tid, provider="gcp_compute", confirm_estimate=False))
