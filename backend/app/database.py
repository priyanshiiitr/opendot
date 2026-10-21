from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


class Teammate(Base):
    __tablename__ = "teammates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="You are a helpful AI teammate.")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="teammate", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    teammate: Mapped[Teammate] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    runs: Mapped[list["AgentRun"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")
    execution_events: Mapped[list["ExecutionEvent"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ExecutionEvent.created_at"
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    status: Mapped[str] = mapped_column(String(30), default="running")
    cancelled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    conversation: Mapped[Conversation] = relationship(back_populates="runs")


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="completed")
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    conversation: Mapped[Conversation] = relationship(back_populates="execution_events")


class ConnectorRecord(Base):
    """Non-secret connector configuration. Tokens live in the OS credential store only."""

    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Automation(Base):
    """Definition of a durable automation (what + when), separate from a single run."""

    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    instruction: Mapped[str] = mapped_column(Text)
    teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Calcutta")
    schedule_kind: Mapped[str] = mapped_column(String(40), default="manual")
    schedule_json: Mapped[str] = mapped_column(Text, default="{}")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missed_policy: Mapped[str] = mapped_column(String(40), default="skip_catch_up")
    include_previous_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    autonomy_policy: Mapped[str] = mapped_column(String(40), default="ask")  # ask | safe_auto
    max_iterations: Mapped[int] = mapped_column(Integer, default=6)
    max_tool_calls: Mapped[int] = mapped_column(Integer, default=12)
    max_run_seconds: Mapped[float] = mapped_column(Float, default=600.0)
    run_on_startup: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    runs: Mapped[list["AutomationRun"]] = relationship(back_populates="automation", cascade="all, delete-orphan")


class AutomationRun(Base):
    """One durable execution of an automation (queue row + receipt)."""

    __tablename__ = "automation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    trigger_type: Mapped[str] = mapped_column(String(40), default="manual")
    occurrence_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    instruction_snapshot: Mapped[str] = mapped_column(Text, default="")
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    automation: Mapped[Automation] = relationship(back_populates="runs")


class AutomationWebhook(Base):
    __tablename__ = "automation_webhooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id"))
    name: Mapped[str] = mapped_column(String(120), default="Webhook")
    secret_hash: Mapped[str] = mapped_column(String(128))
    secret_prefix: Mapped[str] = mapped_column(String(12), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "event_id", name="ux_webhook_deliveries_event"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    webhook_id: Mapped[str] = mapped_column(ForeignKey("automation_webhooks.id"))
    event_id: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("automation_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AppNotification(Base):
    __tablename__ = "app_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    automation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def _engine_url() -> str:
    settings = get_settings()
    if settings.database_url.startswith("sqlite:///./"):
        Path("data").mkdir(exist_ok=True)
    return settings.database_url


engine = create_engine(_engine_url(), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from .migrations import apply_migrations

    apply_migrations(engine, SessionLocal)
    with SessionLocal() as db:
        if db.query(Teammate).count() == 0:
            db.add(
                Teammate(
                    name="Atlas",
                    description="Your general-purpose AI teammate",
                    system_prompt=(
                        "You are Atlas, a concise and thoughtful personal AI teammate. "
                        "Help the user reason clearly and take safe, practical next steps."
                    ),
                )
            )
            db.commit()


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
