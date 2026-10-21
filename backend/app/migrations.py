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


MIGRATIONS: list[tuple[str, Callable]] = [
    ("2026_10_m5_automations", _migrate_m5_automations),
    ("2026_10_m6_computers", _migrate_m6_computers),
    ("2026_10_m7_memory_skills_workflows", _migrate_m7_memory_skills_workflows),
]
