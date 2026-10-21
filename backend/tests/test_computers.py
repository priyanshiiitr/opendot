"""Milestone 6: per-teammate computers, workspace isolation, takeover."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.browser.session import BrowserServiceError
from app.computers.manager import ComputerManager
from app.computers.workspace import WorkspaceError, WorkspaceStore
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db
from app.main import app, computer_manager


@pytest.fixture(autouse=True)
def _iso_db(tmp_path, monkeypatch):
    db_path = tmp_path / "m6.db"
    root = tmp_path / "computers"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_COMPUTERS_ROOT_DIR", str(root))
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
        automation_worker_enabled=False,
        model_provider="mock",
    )
    policy = NavigationPolicy(fixture_mode=False)
    mgr = ComputerManager(settings, database_mod.SessionLocal, policy)
    main_mod.computer_manager = mgr
    # Rebind global used by routes
    import app.main as m

    m.computer_manager = mgr
    globals_computer = mgr
    _ = globals_computer
    computer_manager.root = root  # may be stale; prefer m.computer_manager
    init_db()
    yield {"root": root, "manager": mgr, "settings": settings, "SessionLocal": database_mod.SessionLocal}
    get_settings.cache_clear()


def test_stable_computer_per_teammate(_iso_db):
    mgr: ComputerManager = _iso_db["manager"]
    SessionLocal = _iso_db["SessionLocal"]
    with SessionLocal() as db:
        t1 = db.query(Teammate).first()
        t2 = Teammate(name="Research", description="r", system_prompt="research")
        db.add(t2)
        db.commit()
        db.refresh(t2)
        c1 = mgr.get_or_create_record(db, t1.id)
        c1b = mgr.get_or_create_record(db, t1.id)
        c2 = mgr.get_or_create_record(db, t2.id)
        assert c1.id == c1b.id
        assert c1.id != c2.id
        assert c1.root_path != c2.root_path
        assert Path(c1.root_path).exists()
        assert (Path(c1.root_path) / "workspace").is_dir()


def test_workspace_path_traversal_rejected(_iso_db):
    root = _iso_db["root"] / "x"
    store = WorkspaceStore(root / "workspace", root / "artifacts")
    store.write_text("ok.md", "# hi")
    with pytest.raises(WorkspaceError):
        store.read_text("../ok.md")
    with pytest.raises(WorkspaceError):
        store.read_text("..\\secret.txt")
    with pytest.raises(WorkspaceError):
        store.write_text("/etc/passwd", "nope")


def test_cross_teammate_workspace_isolation(_iso_db):
    mgr: ComputerManager = _iso_db["manager"]
    SessionLocal = _iso_db["SessionLocal"]
    with SessionLocal() as db:
        a = db.query(Teammate).first()
        b = Teammate(name="Other", description="", system_prompt="x")
        db.add(b)
        db.commit()
        db.refresh(b)
        mgr.get_or_create_record(db, a.id)
        mgr.get_or_create_record(db, b.id)
    wa = mgr.workspace_for_teammate(a.id)
    wb = mgr.workspace_for_teammate(b.id)
    wa.write_text("secret.md", "teammate-a-only")
    files_b = wb.list_files()
    assert all(f["path"] != "secret.md" for f in files_b)
    with pytest.raises(WorkspaceError):
        wb.read_text("secret.md")


def test_takeover_blocks_agent_browser_actions(_iso_db):
    mgr: ComputerManager = _iso_db["manager"]
    SessionLocal = _iso_db["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id

    async def scenario():
        await mgr.start(tid, headed=False)
        status = await mgr.takeover(tid)
        assert status["state"] == "human_control"
        assert status["human_control"] is True
        browser = await mgr.session_for_teammate(tid)
        with pytest.raises(BrowserServiceError, match="Human control|paused"):
            await browser.open("https://example.com")
        released = await mgr.release_control(tid)
        assert released["human_control"] is False
        await mgr.stop(tid)

    asyncio.run(scenario())


def test_reconcile_clears_human_control(_iso_db):
    mgr: ComputerManager = _iso_db["manager"]
    SessionLocal = _iso_db["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
        rec = mgr.get_or_create_record(db, tid)
        rec.state = "human_control"
        rec.human_control = True
        db.commit()
    mgr.reconcile_on_startup()
    with SessionLocal() as db:
        row = db.query(ComputerRecord).filter_by(teammate_id=tid).one()
        assert row.human_control is False
        assert row.state == "stopped"


def test_api_computer_and_teammate_create(_iso_db):
    with TestClient(app) as client:
        # Patch app to use fixture manager
        from app import main as main_mod

        main_mod.computer_manager = _iso_db["manager"]
        created = client.post("/api/teammates", json={"name": "Jobs", "description": "search"})
        assert created.status_code == 200
        tid = created.json()["id"]
        status = client.get(f"/api/teammates/{tid}/computer")
        assert status.status_code == 200
        body = status.json()
        assert body["teammate_id"] == tid
        assert body["provider"] == "local_browser"
        assert "workspace" in body["workspace_path"]


def test_clear_browser_requires_confirm(_iso_db):
    mgr: ComputerManager = _iso_db["manager"]
    SessionLocal = _iso_db["SessionLocal"]
    with SessionLocal() as db:
        tid = db.query(Teammate).first().id
    with pytest.raises(Exception):
        asyncio.run(mgr.clear_browser_data(tid, confirm=False))
    result = asyncio.run(mgr.clear_browser_data(tid, confirm=True))
    assert result.get("cleared") is True
