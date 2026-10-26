"""API: provision cloud_sandbox with estimate confirm (Fake Daytona — no network)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.daytona_client import FakeDaytonaClient
from app.computers.manager import ComputerManager
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db
from app.main import app


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    db_path = tmp_path / "api_cs.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE", "false")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED", "true")
    get_settings.cache_clear()
    from app import database as database_mod
    from app import main as main_mod

    database_mod.engine = create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    main_mod.SessionLocal = database_mod.SessionLocal
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        automation_worker_enabled=False,
        model_provider="mock",
        computer_allow_billable=False,
        computer_cloud_sandbox_enabled=True,
        computer_default_provider="local_browser",
    )
    fake = FakeDaytonaClient()
    mgr = ComputerManager(
        settings,
        database_mod.SessionLocal,
        NavigationPolicy(fixture_mode=False),
        daytona_client=fake,
    )
    main_mod.computer_manager = mgr
    main_mod.settings = settings
    init_db()
    client = TestClient(app)
    yield {"client": client, "fake": fake, "SessionLocal": database_mod.SessionLocal}
    get_settings.cache_clear()


def test_api_provision_cloud_sandbox_estimate_then_create(api_env):
    client: TestClient = api_env["client"]
    fake: FakeDaytonaClient = api_env["fake"]
    SessionLocal = api_env["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).first()
        if row:
            db.delete(row)
            db.commit()

    estimate = client.post(
        f"/api/teammates/{tid}/computer/provision",
        json={"provider": "cloud_sandbox", "confirm_estimate": False},
    )
    assert estimate.status_code == 200, estimate.text
    body = estimate.json()
    assert body.get("requires_confirmation") is True
    assert body["provider"] == "cloud_sandbox"
    assert fake.create_calls == 0
    assert body.get("limits", {}).get("cloud_sandbox_enabled") is True
    assert body.get("limits", {}).get("billable_allowed") is False

    created = client.post(
        f"/api/teammates/{tid}/computer/provision",
        json={"provider": "cloud_sandbox", "confirm_estimate": True},
    )
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["provider"] == "cloud_sandbox"
    assert fake.create_calls == 1
    assert data.get("provision", {}).get("created") is True or data.get("provision", {}).get("sandbox_id")
