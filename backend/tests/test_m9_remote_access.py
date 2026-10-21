"""Milestone 9: remote pairing, session auth, lifecycle independence (no public tunnel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.manager import ComputerManager
from app.config import Settings, get_settings
from app.database import PairingChallenge, init_db
from app.remote.service import RemoteAccessService, _hash_secret


@pytest.fixture
def m9_env(tmp_path, monkeypatch):
    db_path = tmp_path / "m9.db"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("AI_TEAMMATES_REMOTE_ACCESS_ENABLED", "true")
    monkeypatch.setenv("AI_TEAMMATES_LOCAL_TRUST_LOOPBACK", "true")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(tmp_path / "computers"))
    get_settings.cache_clear()

    from app import database as database_mod
    from app import main as main_mod

    database_mod.engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    main_mod.SessionLocal = database_mod.SessionLocal
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        automation_worker_enabled=False,
        model_provider="mock",
        remote_access_enabled=True,
        local_trust_loopback=True,
        computers_root_dir=str(tmp_path / "computers"),
    )
    main_mod.settings = settings
    main_mod.remote_access = RemoteAccessService(settings)
    mgr = ComputerManager(settings, database_mod.SessionLocal, NavigationPolicy(fixture_mode=False))
    main_mod.computer_manager = mgr
    main_mod.runtime.computer_manager = mgr
    init_db()
    yield {"settings": settings, "SessionLocal": database_mod.SessionLocal, "main": main_mod}
    get_settings.cache_clear()


def test_remote_disabled_by_default_blocks_pairing_start(tmp_path, monkeypatch):
    db_path = tmp_path / "off.db"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_REMOTE_ACCESS_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    from app import database as database_mod
    from app import main as main_mod

    database_mod.engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    main_mod.SessionLocal = database_mod.SessionLocal
    settings = Settings(database_url=f"sqlite:///{db_path.as_posix()}", remote_access_enabled=False, automation_worker_enabled=False)
    main_mod.settings = settings
    main_mod.remote_access = RemoteAccessService(settings)
    init_db()
    with TestClient(main_mod.app) as client:
        assert client.get("/api/remote/status").json()["remote_access_enabled"] is False
        assert client.post("/api/remote/pairing/start").status_code == 400
    get_settings.cache_clear()


def test_pairing_complete_reuse_and_expiry(m9_env):
    main = m9_env["main"]
    SessionLocal = m9_env["SessionLocal"]
    with TestClient(main.app) as client:
        started = client.post("/api/remote/pairing/start").json()
        code = started["code"]
        assert "code" in started
        done = client.post(
            "/api/remote/pairing/complete",
            json={"code": code, "device_name": "Pixel"},
        ).json()
        token = done["token"]
        assert token
        # Reuse fails
        reuse = client.post("/api/remote/pairing/complete", json={"code": code, "device_name": "Other"})
        assert reuse.status_code == 401
        # Valid session can list diagnostics on loopback (trusted)
        assert client.get("/api/diagnostics").status_code == 200
        devices = client.get("/api/remote/devices").json()
        assert any(d["device_name"] == "Pixel" for d in devices)
        # Revoke
        sid = done["session_id"]
        assert client.delete(f"/api/remote/devices/{sid}").json()["revoked"] is True
        # Expired challenge
        with SessionLocal() as db:
            svc = main.remote_access
            out = svc.start_pairing(db, client_key="127.0.0.1")
            row = db.get(PairingChallenge, out["challenge_id"])
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            db.commit()
            code2 = out["code"]
        expired = client.post("/api/remote/pairing/complete", json={"code": code2, "device_name": "Late"})
        assert expired.status_code == 401


def test_non_loopback_requires_session(m9_env, monkeypatch):
    main = m9_env["main"]
    # Force authorize_request to treat client as non-loopback by patching is_loopback
    import app.remote.access as access_mod
    import app.main as main_mod

    monkeypatch.setattr(access_mod, "is_loopback", lambda _req: False)
    monkeypatch.setattr(main_mod, "is_loopback", lambda _req: False)

    with TestClient(main.app) as client:
        denied = client.get("/api/teammates")
        assert denied.status_code == 401
        # Pairing complete still public
        start = client.post("/api/remote/pairing/start")
        # start requires loopback — also denied when is_loopback false
        assert start.status_code == 403
        # Create pairing via service directly
        with m9_env["SessionLocal"]() as db:
            started = main.remote_access.start_pairing(db, client_key="test")
            code = started["code"]
        paired = client.post(
            "/api/remote/pairing/complete",
            json={"code": code, "device_name": "Phone"},
        ).json()
        token = paired["token"]
        ok = client.get("/api/teammates", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        # Revoked token fails
        client.delete(f"/api/remote/devices/{paired['session_id']}", headers={"Authorization": f"Bearer {token}"})
        again = client.get("/api/teammates", headers={"Authorization": f"Bearer {token}"})
        assert again.status_code == 401


def test_ready_and_health(m9_env):
    with TestClient(m9_env["main"].app) as client:
        assert client.get("/health").json()["status"] == "ok"
        ready = client.get("/ready").json()
        assert ready["ready"] is True
        assert ready["database"] is True


def test_token_hash_not_plaintext():
    assert _hash_secret("abc") != "abc"
    assert len(_hash_secret("abc")) == 64
