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
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    role_label: Mapped[str] = mapped_column(String(80), default="")
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
    link_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # safe UI deep-link hint
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PairingChallenge(Base):
    """Short-lived pairing code (store hash only after issuance)."""

    __tablename__ = "pairing_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DeviceSession(Base):
    """Paired companion device session (token hash only)."""

    __tablename__ = "device_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_name: Mapped[str] = mapped_column(String(120), default="device")
    token_hash: Mapped[str] = mapped_column(String(128))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")  # never secrets
    device_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ComputerRecord(Base):
    """Persistent metadata for a teammate's computer (local browser or isolated Linux-dev)."""

    __tablename__ = "computers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(40), default="local_browser")
    isolation_tier: Mapped[str] = mapped_column(String(40), default="local_browser")
    control_state: Mapped[str] = mapped_column(String(40), default="agent_control")
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_meta_json: Mapped[str] = mapped_column(Text, default="{}")
    state: Mapped[str] = mapped_column(String(40), default="stopped")
    root_path: Mapped[str] = mapped_column(Text, default="")
    last_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_action: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_task: Mapped[str | None] = mapped_column(String(200), nullable=True)
    human_control: Mapped[bool] = mapped_column(Boolean, default=False)
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryRecord(Base):
    """Scoped, inspectable persistent memory (not full conversation history)."""

    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_type: Mapped[str] = mapped_column(String(40))  # preference | teammate | project | task | fact
    scope: Mapped[str] = mapped_column(String(40))  # user | teammate | project | task
    scope_id: Mapped[str] = mapped_column(String(120), default="default")
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="explicit")  # explicit | inferred
    status: Mapped[str] = mapped_column(String(40), default="active")  # active | superseded | deleted
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    source_conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SkillInstall(Base):
    """Installed/enabled skill metadata; instruction body lives on disk by skill_id@version."""

    __tablename__ = "skill_installs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    teammate_id: Mapped[int | None] = mapped_column(ForeignKey("teammates.id"), nullable=True)
    package_path: Mapped[str] = mapped_column(Text, default="")
    allowed_tool_categories_json: Mapped[str] = mapped_column(Text, default="[]")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class ConnectorGrant(Base):
    """Which teammate may use which connector instance / operations."""

    __tablename__ = "connector_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    connector_id: Mapped[str] = mapped_column(String(64))
    operations_json: Mapped[str] = mapped_column(Text, default="[]")  # e.g. ["telegram_get_bot_info", ...]
    resource_scope_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class WorkflowRun(Base):
    """Multi-step single-agent workflow (extends durable execution beyond one chat turn)."""

    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    automation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="Workflow")
    instruction: Mapped[str] = mapped_column(Text, default="")
    skill_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    memory_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    report_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    step_key: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    depends_on_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Team(Base):
    """Configurable roster: lead + authorized members (not a hardcoded five-agent cast)."""

    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    lead_teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    member_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_subtasks: Mapped[int] = mapped_column(Integer, default=12)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=2)
    max_depth: Mapped[int] = mapped_column(Integer, default=1)  # 1 = specialists cannot re-delegate
    max_retries: Mapped[int] = mapped_column(Integer, default=1)
    max_total_runs: Mapped[int] = mapped_column(Integer, default=20)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class TeamRun(Base):
    """Parent collaborative execution — survives restart."""

    __tablename__ = "team_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    lead_teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str] = mapped_column(Text, default="{}")
    shared_artifact_dir: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeamSubtask(Base):
    __tablename__ = "team_subtasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_run_id: Mapped[str] = mapped_column(ForeignKey("team_runs.id"))
    subtask_key: Mapped[str] = mapped_column(String(80))
    assignee_teammate_id: Mapped[int] = mapped_column(ForeignKey("teammates.id"))
    objective: Mapped[str] = mapped_column(Text)
    output_format: Mapped[str] = mapped_column(Text, default="structured_findings")
    depends_on_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    depth: Mapped[int] = mapped_column(Integer, default=1)
    critical: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_tool_categories_json: Mapped[str] = mapped_column(Text, default='["local","workspace"]')
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    artifact_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TeamEvent(Base):
    __tablename__ = "team_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_run_id: Mapped[str] = mapped_column(ForeignKey("team_runs.id"))
    subtask_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
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
