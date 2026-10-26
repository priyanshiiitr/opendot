"""Migration slice: durable job contract + cloud_sandbox stub (no real cloud)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.automations.dispatcher import (
    ExternalDurableNotConfigured,
    JobDispatcher,
)
from app.automations.jobs import FUTURE_EXTERNAL_BACKEND_REQUIREMENTS, JobEnvelope
from app.automations.worker import AutomationWorker
from app.browser.policy import NavigationPolicy
from app.computers.cloud_sandbox import DAYTONA_ADAPTER_METHODS
from app.computers.manager import ComputerError, ComputerManager, SUPPORTED_PROVIDERS
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db
from app.permissions import PermissionBroker
from app.runtime import AgentRuntime
from app.tools import ToolRegistry


@pytest.fixture
def slice_env(tmp_path, monkeypatch):
    db_path = tmp_path / "slice.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_DEFAULT_PROVIDER", "local_browser")
    monkeypatch.setenv("AI_TEAMMATES_DURABLE_EXECUTION_BACKEND", "in_process")
    monkeypatch.setenv("AI_TEAMMATES_DURABLE_EXTERNAL_BACKEND_ENABLED", "false")
    get_settings.cache_clear()
    from app import database as database_mod

    database_mod.engine = create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        automation_worker_enabled=False,
        model_provider="mock",
        computer_allow_billable=False,
        computer_credit_funded_enabled=False,
        computer_cloud_sandbox_enabled=False,
        computer_default_provider="local_browser",
        durable_execution_backend="in_process",
        durable_external_backend_enabled=False,
    )
    policy = NavigationPolicy(fixture_mode=False)
    mgr = ComputerManager(settings, database_mod.SessionLocal, policy)
    init_db()
    yield {
        "root": root,
        "manager": mgr,
        "SessionLocal": database_mod.SessionLocal,
        "settings": settings,
    }
    get_settings.cache_clear()


def test_default_provider_remains_local_browser(slice_env):
    settings: Settings = slice_env["settings"]
    assert settings.computer_default_provider == "local_browser"
    assert "cloud_sandbox" in SUPPORTED_PROVIDERS
    assert settings.computer_allow_billable is False
    assert settings.computer_cloud_sandbox_enabled is False

    mgr: ComputerManager = slice_env["manager"]
    SessionLocal = slice_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()

    status = asyncio.run(mgr.provision(tid))
    assert status["provider"] == "local_browser"
    assert status["isolation_tier"] == "local_browser"


def test_cloud_sandbox_refuses_without_enable_flag(slice_env):
    mgr: ComputerManager = slice_env["manager"]
    SessionLocal = slice_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()

    with pytest.raises(ComputerError, match="cloud_sandbox refuses|CLOUD_SANDBOX_ENABLED"):
        asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=False))


def test_cloud_sandbox_refuses_confirm_without_api_key(tmp_path, monkeypatch):
    """Enable flag + confirm still refuses when no Daytona credentials are present."""
    db_path = tmp_path / "enabled.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED", "true")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("AI_TEAMMATES_DAYTONA_API_KEY", raising=False)
    get_settings.cache_clear()
    from app import database as database_mod
    from app.computers.daytona_client import FakeDaytonaClient

    database_mod.engine = create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        automation_worker_enabled=False,
        model_provider="mock",
        computer_allow_billable=False,
        computer_cloud_sandbox_enabled=True,
        computer_default_provider="local_browser",
        daytona_api_key="",
    )
    no_creds = FakeDaytonaClient()
    no_creds.credentials_ok = False
    mgr = ComputerManager(
        settings,
        database_mod.SessionLocal,
        NavigationPolicy(fixture_mode=False),
        daytona_client=no_creds,
    )
    init_db()
    with database_mod.SessionLocal() as db:
        tid = db.query(Teammate).first().id

    pending = asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=False))
    assert pending.get("requires_confirmation") is True
    assert pending["provider"] == "cloud_sandbox"

    with pytest.raises(ComputerError, match="API key|not found|No sandbox"):
        asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))

    assert "provision" in DAYTONA_ADAPTER_METHODS
    get_settings.cache_clear()


def test_job_dispatcher_defaults_to_in_process(slice_env):
    settings: Settings = slice_env["settings"]
    worker = AutomationWorker(settings, slice_env["SessionLocal"], runtime=None, service=None)  # type: ignore[arg-type]
    # Worker only needs settings for describe/start when disabled.
    dispatcher = JobDispatcher(settings, worker)
    assert dispatcher.active_name == "in_process"
    desc = dispatcher.describe()
    assert desc["active"] == "in_process"
    assert desc["backends"]["in_process"]["backend"] == "in_process"
    assert desc["backends"]["external_stub"]["implemented"] is False
    env = JobEnvelope(job_type="automation_run", run_id="r1", teammate_id=1)
    assert env.as_dict()["job_type"] == "automation_run"
    assert "leased_claim" in FUTURE_EXTERNAL_BACKEND_REQUIREMENTS


def test_external_durable_stub_refuses_start(slice_env):
    settings = Settings(
        database_url=slice_env["settings"].database_url,
        computers_root_dir=slice_env["settings"].computers_root_dir,
        automation_worker_enabled=False,
        model_provider="mock",
        durable_execution_backend="external_stub",
        durable_external_backend_enabled=False,
    )
    worker = AutomationWorker(settings, slice_env["SessionLocal"], runtime=None, service=None)  # type: ignore[arg-type]
    dispatcher = JobDispatcher(settings, worker)
    with pytest.raises(ExternalDurableNotConfigured, match="disabled|External durable"):
        asyncio.run(dispatcher.start())


def test_agent_runtime_and_permission_broker_with_cloud_sandbox_registered(slice_env):
    """New provider registration must not break orchestration engine wiring."""
    assert "cloud_sandbox" in SUPPORTED_PROVIDERS
    settings: Settings = slice_env["settings"]
    mgr: ComputerManager = slice_env["manager"]
    broker = PermissionBroker()
    tools = ToolRegistry("Asia/Calcutta", 1, 1000, 1000)
    runtime = AgentRuntime(settings, tools, broker, mgr, memory_service=None)
    assert runtime.computer_manager is mgr
    assert runtime.permissions is broker

    decision = broker.assess(tools.get("get_current_time"))
    assert decision.status == "allowed"
    decision_demo = broker.assess(tools.get("demo_request_approval"))
    assert decision_demo.status == "approval_required"

    SessionLocal = slice_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()
    with pytest.raises(ComputerError, match="cloud_sandbox refuses|CLOUD_SANDBOX_ENABLED"):
        asyncio.run(mgr.provision(tid, provider="cloud_sandbox"))
