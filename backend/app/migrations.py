"""Lightweight, non-destructive schema migrations for the local SQLite workspace.

Why not full Alembic yet: existing installs already used ``create_all``. Additive
``CREATE TABLE IF NOT EXISTS`` plus a versioned runner covers M5 safely without
stamping or rewriting the user database. Alembic remains the path for multi-host
deploys (see ADR 0007).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .database import Base, SchemaMigration


def apply_migrations(engine: Engine, session_factory) -> None:
    """Create missing tables, then run ordered SQL migrations once each."""
    Base.metadata.create_all(engine)
    with session_factory() as db:
        _ensure_migration_table(db)
        applied = {row.version for row in db.query(SchemaMigration).all()}
        for version, fn in MIGRATIONS:
            if version in applied:
                continue
            fn(engine, db)
            db.add(SchemaMigration(version=version, applied_at=datetime.now(timezone.utc)))
            db.commit()


def _ensure_migration_table(db: Session) -> None:
    # create_all already creates schema_migrations when the model is registered.
    db.commit()


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _migrate_m5_automations(engine: Engine, db: Session) -> None:
    """Ensure automation tables exist; no-op if create_all already added them."""
    required = {
        "automations",
        "automation_runs",
        "automation_webhooks",
        "webhook_deliveries",
        "app_notifications",
        "schema_migrations",
    }
    missing = required - _table_names(engine)
    if missing:
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in missing if name in Base.metadata.tables])
    # Index / uniqueness helpers that create_all may already define — keep idempotent.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_automation_runs_occurrence "
                "ON automation_runs (occurrence_key) WHERE occurrence_key IS NOT NULL"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_webhook_deliveries_event "
                "ON webhook_deliveries (webhook_id, event_id)"
            )
        )


def _migrate_m6_computers(engine: Engine, db: Session) -> None:
    required = {"computers"}
    missing = required - _table_names(engine)
    if missing:
        Base.metadata.create_all(
            engine, tables=[Base.metadata.tables[name] for name in missing if name in Base.metadata.tables]
        )
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_computers_teammate ON computers (teammate_id)"))


def _migrate_m7_memory_skills_workflows(engine: Engine, db: Session) -> None:
    required = {"memories", "skill_installs", "connector_grants", "workflow_runs", "workflow_steps"}
    missing = required - _table_names(engine)
    if missing:
        Base.metadata.create_all(
            engine, tables=[Base.metadata.tables[name] for name in missing if name in Base.metadata.tables]
        )
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_scope ON memories (scope, scope_id, status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grants_teammate ON connector_grants (teammate_id, connector_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workflow_steps_wf ON workflow_steps (workflow_id, status)"))


def _migrate_m8_teams(engine: Engine, db: Session) -> None:
    """Teams + parent/child runs; additive teammate columns for enable/role."""
    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(engine).get_columns("teammates")}
        if "enabled" not in cols:
            conn.execute(text("ALTER TABLE teammates ADD COLUMN enabled BOOLEAN DEFAULT 1 NOT NULL"))
        if "role_label" not in cols:
            conn.execute(text("ALTER TABLE teammates ADD COLUMN role_label VARCHAR(80) DEFAULT ''"))
    required = {"teams", "team_runs", "team_subtasks", "team_events"}
    missing = required - _table_names(engine)
    if missing:
        Base.metadata.create_all(
            engine, tables=[Base.metadata.tables[name] for name in missing if name in Base.metadata.tables]
        )
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_team_subtasks_run ON team_subtasks (team_run_id, status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_team_events_run ON team_events (team_run_id, created_at)"))


def _migrate_m9_remote_access(engine: Engine, db: Session) -> None:
    required = {"pairing_challenges", "device_sessions", "security_audit_events"}
    missing = required - _table_names(engine)
    if missing:
        Base.metadata.create_all(
            engine, tables=[Base.metadata.tables[name] for name in missing if name in Base.metadata.tables]
        )
    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(engine).get_columns("app_notifications")}
        if "link_ref" not in cols:
            conn.execute(text("ALTER TABLE app_notifications ADD COLUMN link_ref VARCHAR(200)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_device_sessions_hash ON device_sessions (token_hash)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pairing_expires ON pairing_challenges (expires_at)"))


MIGRATIONS: list[tuple[str, Callable]] = [
    ("2026_10_m5_automations", _migrate_m5_automations),
    ("2026_10_m6_computers", _migrate_m6_computers),
    ("2026_10_m7_memory_skills_workflows", _migrate_m7_memory_skills_workflows),
    ("2026_10_m8_multi_agent_teams", _migrate_m8_teams),
    ("2026_10_m9_remote_access", _migrate_m9_remote_access),
]
