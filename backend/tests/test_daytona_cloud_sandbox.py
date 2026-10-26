"""Daytona cloud_sandbox adapter tests (Fake client — never calls Daytona network)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.cloud_sandbox import assert_cloud_sandbox_allowed
from app.computers.daytona_client import FakeDaytonaClient
from app.computers.manager import ComputerError, ComputerManager
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db


@pytest.fixture
def daytona_env(tmp_path, monkeypatch):
    db_path = tmp_path / "daytona.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("AI_TEAMMATES_DAYTONA_MAX_CONCURRENT_SANDBOXES", "1")
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
        computer_cloud_sandbox_enabled=True,
        daytona_max_concurrent_sandboxes=1,
        computer_default_provider="local_browser",
    )
    fake = FakeDaytonaClient()
    mgr = ComputerManager(
        settings,
        database_mod.SessionLocal,
        NavigationPolicy(fixture_mode=False),
        daytona_client=fake,
    )
    init_db()
    yield {
        "manager": mgr,
        "SessionLocal": database_mod.SessionLocal,
        "fake": fake,
        "settings": settings,
    }
    get_settings.cache_clear()


def _clear(SessionLocal, tid: int) -> None:
    with SessionLocal() as db:
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()


def test_policy_refuses_when_disabled(tmp_path):
    settings = Settings(computer_cloud_sandbox_enabled=False, computer_allow_billable=False)
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'p.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    from app.database import Base

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        with pytest.raises(Exception, match="disabled|CLOUD_SANDBOX_ENABLED"):
            assert_cloud_sandbox_allowed(settings, db)


def test_policy_refuses_when_billable_true(daytona_env):
    settings = daytona_env["settings"].model_copy(update={"computer_allow_billable": True})
    with daytona_env["SessionLocal"]() as db:
        with pytest.raises(Exception, match="ALLOW_BILLABLE|not authorized|Refusing"):
            assert_cloud_sandbox_allowed(settings, db)


def test_estimate_then_create_idempotent(daytona_env):
    mgr: ComputerManager = daytona_env["manager"]
    fake: FakeDaytonaClient = daytona_env["fake"]
    SessionLocal = daytona_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear(SessionLocal, tid)

    pending = asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=False))
    assert pending.get("requires_confirmation") is True
    assert fake.create_calls == 0

    created = asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))
    assert created["provider"] == "cloud_sandbox"
    assert created["provision"]["created"] is True
    assert fake.create_calls == 1
    sid = created["provision"]["sandbox_id"]

    again = asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))
    assert again["provision"].get("idempotent") is True
    assert again["provision"]["sandbox_id"] == sid
    assert fake.create_calls == 1

    health = asyncio.run(mgr.health(tid))
    assert health["health"]["healthy"] is True
    assert health["health"]["vendor"] == "daytona"


def test_default_provider_still_local_browser(daytona_env):
    assert daytona_env["settings"].computer_default_provider == "local_browser"
    mgr: ComputerManager = daytona_env["manager"]
    SessionLocal = daytona_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear(SessionLocal, tid)
    status = asyncio.run(mgr.provision(tid))
    assert status["provider"] == "local_browser"
    assert daytona_env["fake"].create_calls == 0


def test_computer_use_preview_png(daytona_env):
    mgr: ComputerManager = daytona_env["manager"]
    fake: FakeDaytonaClient = daytona_env["fake"]
    SessionLocal = daytona_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear(SessionLocal, tid)
    asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))
    started = asyncio.run(mgr.start(tid, headed=False))
    assert started["provider"] == "cloud_sandbox"
    assert fake.computer_use_starts >= 1
    png = asyncio.run(mgr.preview(tid))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert fake.screenshot_calls >= 1


def test_desktop_tools_click_type_and_permissions(daytona_env):
    from app.computers.context import current_teammate_id
    from app.computers.desktop_tools import register_desktop_tools
    from app.permissions import PermissionBroker
    from app.tools import ToolRegistry

    mgr: ComputerManager = daytona_env["manager"]
    fake: FakeDaytonaClient = daytona_env["fake"]
    SessionLocal = daytona_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear(SessionLocal, tid)
    asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))

    registry = ToolRegistry("Asia/Calcutta", 5, 8000, 24000, computer_manager=None)
    register_desktop_tools(registry, mgr, 5.0)
    broker = PermissionBroker()
    assert broker.assess(registry.get("desktop_screenshot")).status == "allowed"
    assert broker.assess(registry.get("desktop_open_url")).status == "allowed"
    assert broker.assess(registry.get("desktop_click")).status == "approval_required"
    assert broker.assess(registry.get("desktop_type")).status == "approval_required"

    token = current_teammate_id.set(tid)
    try:
        shot = asyncio.run(registry.execute(registry.get("desktop_screenshot"), registry.get("desktop_screenshot").arguments_model()))
        assert shot["action"] == "desktop_screenshot"
        opened = asyncio.run(
            registry.execute(
                registry.get("desktop_open_url"),
                registry.get("desktop_open_url").arguments_model(url="https://example.com"),
            )
        )
        assert opened["action"] == "desktop_open_url"
        assert opened["url"] == "https://example.com"
        with pytest.raises(Exception, match="blocked|not allowed|localhost|private|scheme"):
            asyncio.run(
                registry.execute(
                    registry.get("desktop_open_url"),
                    registry.get("desktop_open_url").arguments_model(url="http://127.0.0.1/"),
                )
            )
        click = asyncio.run(
            registry.execute(
                registry.get("desktop_click"),
                registry.get("desktop_click").arguments_model(x=10, y=20),
            )
        )
        assert click["action"] == "desktop_click"
        typed = asyncio.run(
            registry.execute(
                registry.get("desktop_type"),
                registry.get("desktop_type").arguments_model(text="hello"),
            )
        )
        assert typed["typed_chars"] == 5
    finally:
        current_teammate_id.reset(token)
    assert fake.click_calls >= 1
    assert fake.type_calls >= 1
    assert any("example.com" in c for c in fake.exec_commands)


def test_runtime_hint_for_cloud_sandbox(daytona_env):
    from app.runtime import AgentRuntime
    from app.permissions import PermissionBroker
    from app.tools import ToolRegistry

    mgr: ComputerManager = daytona_env["manager"]
    SessionLocal = daytona_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        teammate = db.get(Teammate, tid)
    _clear(SessionLocal, tid)
    asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))

    runtime = AgentRuntime(daytona_env["settings"], ToolRegistry("Asia/Calcutta", 1, 1000, 1000), PermissionBroker(), mgr)
    with SessionLocal() as db:
        from app.database import Conversation

        conv = Conversation(teammate_id=tid, title="t")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        # Attach teammate relationship for system prompt
        conv.teammate = db.get(Teammate, tid)
        prompt = runtime._system_prompt(db, conv, "hello")
    assert "cloud_sandbox" in prompt
    assert "desktop_open_url" in prompt
    assert "browser_*" in prompt


def test_delete_removes_remote_and_row(daytona_env):
    mgr: ComputerManager = daytona_env["manager"]
    fake: FakeDaytonaClient = daytona_env["fake"]
    SessionLocal = daytona_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    _clear(SessionLocal, tid)
    asyncio.run(mgr.provision(tid, provider="cloud_sandbox", confirm_estimate=True))
    with pytest.raises(ComputerError, match="confirm"):
        asyncio.run(mgr.delete_computer(tid, confirm=False))
    result = asyncio.run(mgr.delete_computer(tid, confirm=True))
    assert result["deleted"] is True
    assert fake.delete_calls >= 1
    with SessionLocal() as db:
        assert db.query(ComputerRecord).filter_by(teammate_id=tid).first() is None
