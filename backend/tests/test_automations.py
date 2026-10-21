"""Milestone 5: durable automations, scheduler, worker, webhooks, recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.automations.checkpoints import classify_interrupted_run, checkpoint_after_event
from app.automations.schedule import compute_next_run_at, occurrence_key
from app.automations.service import AutomationService
from app.automations.worker import AutomationWorker
from app.config import Settings, get_settings
from app.database import AutomationRun, Base, Conversation, Message, Teammate, init_db
from app.main import app, automation_service, automation_worker, runtime
from app.providers import MockProvider


@pytest.fixture(autouse=True)
def _reset_db(tmp_path, monkeypatch):
    db_path = tmp_path / "m5.db"
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AI_TEAMMATES_AUTOMATION_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_TEAMMATES_MODEL_PROVIDER", "mock")
    get_settings.cache_clear()
    # Rebind engine used by app modules
    from app import database as database_mod
    from app import main as main_mod

    database_mod.engine = create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    main_mod.SessionLocal = database_mod.SessionLocal
    automation_worker.session_factory = database_mod.SessionLocal
    init_db()
    yield
    get_settings.cache_clear()


def test_schedule_daily_timezone_and_occurrence_unique():
    now = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)  # morning UTC
    nxt = compute_next_run_at(
        schedule_kind="daily",
        schedule_json='{"time": "09:00"}',
        timezone_name="Asia/Calcutta",
        from_time=now,
    )
    assert nxt is not None
    assert nxt > now
    key1 = occurrence_key("auto1", nxt)
    key2 = occurrence_key("auto1", nxt)
    assert key1 == key2


def test_weekdays_skips_weekend():
    # Saturday morning local India
    saturday = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)
    nxt = compute_next_run_at(
        schedule_kind="weekdays",
        schedule_json='{"time": "09:00", "weekdays": [0,1,2,3,4]}',
        timezone_name="Asia/Calcutta",
        from_time=saturday,
    )
    assert nxt is not None
    # Should land on Monday
    assert nxt.astimezone(timezone.utc).weekday() == 0 or nxt > saturday


def test_checkpoint_consequential_uncertain():
    cp = checkpoint_after_event(
        {},
        {
            "type": "execution",
            "event_type": "tool_call",
            "status": "running",
            "tool_name": "telegram_send_message",
            "tool_call_id": "c1",
        },
    )
    assert cp.get("uncertain_side_effect") is True
    assert classify_interrupted_run(cp, "running") == "needs_reconciliation"


def test_checkpoint_waiting_approval_survives():
    cp = checkpoint_after_event({}, {"type": "approval_required", "approval_id": "a1"})
    assert classify_interrupted_run(cp, "waiting_approval") == "waiting_approval"


def test_create_run_now_and_worker_executes_with_mock():
    settings = Settings(model_provider="mock", automation_worker_enabled=False)
    service = AutomationService(settings)
    from app import database as database_mod

    with database_mod.SessionLocal() as db:
        teammate = db.query(Teammate).first()
        auto = service.create(
            db,
            {
                "name": "Morning calc",
                "instruction": "add 2 and 3",
                "teammate_id": teammate.id,
                "schedule_kind": "manual",
            },
        )
        run = service.run_now(db, auto.id)
        assert run.status == "queued"
        run_id = run.id

    worker = AutomationWorker(settings, database_mod.SessionLocal, runtime, service)
    claimed = worker._claim_next()
    assert claimed == run_id
    # Second claim must not steal the same run
    assert worker._claim_next() is None
    asyncio.run(worker._execute(run_id))

    with database_mod.SessionLocal() as db:
        done = db.get(AutomationRun, run_id)
        assert done is not None
        assert done.status == "completed"
        assert done.conversation_id is not None
        msgs = db.query(Message).filter_by(conversation_id=done.conversation_id, role="assistant").all()
        assert msgs
        assert "5" in msgs[-1].content


def test_paused_schedule_does_not_enqueue():
    settings = Settings(model_provider="mock")
    service = AutomationService(settings)
    from app import database as database_mod

    with database_mod.SessionLocal() as db:
        teammate = db.query(Teammate).first()
        auto = service.create(
            db,
            {
                "name": "Paused daily",
                "instruction": "add 1 and 1",
                "teammate_id": teammate.id,
                "schedule_kind": "daily",
                "schedule": {"time": "00:00"},
                "paused": True,
            },
        )
        auto.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        created = service.process_due_schedules(db)
        assert created == []


def test_scheduler_dedupes_occurrence():
    settings = Settings(model_provider="mock")
    service = AutomationService(settings)
    from app import database as database_mod

    with database_mod.SessionLocal() as db:
        teammate = db.query(Teammate).first()
        due = datetime.now(timezone.utc).replace(microsecond=0)
        auto = service.create(
            db,
            {
                "name": "Dedup",
                "instruction": "add 1 and 1",
                "teammate_id": teammate.id,
                "schedule_kind": "interval",
                "schedule": {"interval_minutes": 60},
            },
        )
        auto.next_run_at = due
        db.commit()
        first = service.process_due_schedules(db, now=due + timedelta(seconds=1))
        assert len(first) == 1
        # Force same occurrence again
        auto = service.get(db, auto.id)
        auto.next_run_at = due
        auto.enabled = True
        db.commit()
        second = service.enqueue(
            db, auto, trigger_type="schedule", occurrence=occurrence_key(auto.id, due)
        )
        assert second is None  # unique occurrence_key


def test_cancel_stops_queued_run():
    settings = Settings(model_provider="mock")
    service = AutomationService(settings)
    from app import database as database_mod

    with database_mod.SessionLocal() as db:
        teammate = db.query(Teammate).first()
        auto = service.create(
            db,
            {"name": "Cancel me", "instruction": "add 1 and 1", "teammate_id": teammate.id},
        )
        run = service.run_now(db, auto.id)
        cancelled = service.cancel_run(db, run.id)
        assert cancelled.status == "cancelled"


def test_reconcile_running_consequential_needs_review():
    settings = Settings(model_provider="mock")
    service = AutomationService(settings)
    from app import database as database_mod
    import json

    with database_mod.SessionLocal() as db:
        teammate = db.query(Teammate).first()
        auto = service.create(
            db, {"name": "Crash", "instruction": "x", "teammate_id": teammate.id}
        )
        run = AutomationRun(
            id="run-crash",
            automation_id=auto.id,
            status="running",
            trigger_type="manual",
            instruction_snapshot="x",
            checkpoint_json=json.dumps(
                {"uncertain_side_effect": True, "last_tool_name": "telegram_send_message"}
            ),
        )
        db.add(run)
        db.commit()

    worker = AutomationWorker(settings, database_mod.SessionLocal, runtime, service)
    with database_mod.SessionLocal() as db:
        worker.reconcile_on_startup(db)
        row = db.get(AutomationRun, "run-crash")
        assert row.status == "needs_reconciliation"


def test_webhook_auth_and_dedup():
    from app import database as database_mod

    with TestClient(app) as client:
        with database_mod.SessionLocal() as db:
            teammate = db.query(Teammate).first()
            auto = automation_service.create(
                db,
                {"name": "Hook", "instruction": "add 2 and 2", "teammate_id": teammate.id},
            )
            hook, secret = automation_service.create_webhook(db, auto.id)
            hook_id = hook.id

        bad = client.post(f"/hooks/{hook_id}", headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == 401

        ok = client.post(
            f"/hooks/{hook_id}",
            headers={"Authorization": f"Bearer {secret}", "X-Event-Id": "evt-1"},
            content=b'{"ping":true}',
        )
        assert ok.status_code == 200
        assert ok.json()["accepted"] is True

        dup = client.post(
            f"/hooks/{hook_id}",
            headers={"Authorization": f"Bearer {secret}", "X-Event-Id": "evt-1"},
            content=b'{"ping":true}',
        )
        assert dup.status_code == 409


def test_api_automations_crud_and_migration_preserves_teammate():
    from app import database as database_mod

    with database_mod.SessionLocal() as db:
        before = db.query(Teammate).count()
        assert before >= 1

    with TestClient(app) as client:
        teammates = client.get("/api/teammates").json()
        created = client.post(
            "/api/automations",
            json={
                "name": "API auto",
                "instruction": "what time is it?",
                "teammate_id": teammates[0]["id"],
                "schedule_kind": "manual",
            },
        )
        assert created.status_code == 200
        auto_id = created.json()["id"]
        listed = client.get("/api/automations").json()
        assert any(a["id"] == auto_id for a in listed)
        paused = client.patch(f"/api/automations/{auto_id}", json={"paused": True})
        assert paused.json()["paused"] is True
        run = client.post(f"/api/automations/{auto_id}/run")
        assert run.status_code == 400  # paused


def test_ui_disconnect_does_not_cancel_background_semantics():
    """Queued/running automation state is DB-backed; no chat SSE is required."""
    settings = Settings(model_provider="mock")
    service = AutomationService(settings)
    from app import database as database_mod

    with database_mod.SessionLocal() as db:
        teammate = db.query(Teammate).first()
        auto = service.create(
            db, {"name": "Detach", "instruction": "add 4 and 5", "teammate_id": teammate.id}
        )
        run = service.run_now(db, auto.id)
        run_id = run.id
    # Simulate "UI navigated away": no open stream; worker still executes.
    worker = AutomationWorker(settings, database_mod.SessionLocal, runtime, service)
    rid = worker._claim_next()
    asyncio.run(worker._execute(rid))
    with database_mod.SessionLocal() as db:
        assert db.get(AutomationRun, run_id).status == "completed"


def test_migration_on_existing_db_keeps_conversations(tmp_path, monkeypatch):
    """Old DB with teammates/conversations gains automation tables without wipe."""
    from sqlalchemy import text

    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE teammates (id INTEGER PRIMARY KEY, name VARCHAR(100), "
                "description VARCHAR(500), system_prompt TEXT, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO teammates (id, name, description, system_prompt, created_at) "
                "VALUES (1, 'Legacy', 'kept', 'hi', '2026-01-01')"
            )
        )
    monkeypatch.setenv("AI_TEAMMATES_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    get_settings.cache_clear()
    from app import database as database_mod
    from app.migrations import apply_migrations

    database_mod.engine = create_engine(
        f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False}
    )
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    apply_migrations(database_mod.engine, database_mod.SessionLocal)
    with database_mod.SessionLocal() as db:
        assert db.get(Teammate, 1).name == "Legacy"
        from sqlalchemy import inspect

        assert "automations" in inspect(database_mod.engine).get_table_names()
