from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class TeammateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str


class TeammateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(
        default="You are a helpful AI teammate with your own isolated computer and workspace.",
        max_length=8_000,
    )


class ComputerStatusOut(BaseModel):
    id: str
    teammate_id: int
    provider: str
    state: str
    human_control: bool
    last_url: str | None = None
    last_error: str | None = None
    last_action: str | None = None
    current_task: str | None = None
    workspace_path: str
    artifacts_path: str
    browser_open: bool
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    isolation_note: str = ""
    takeover: dict[str, Any] | None = None
    release: dict[str, Any] | None = None
    cleared: bool | None = None
    message: str | None = None

class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    created_at: datetime

class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    teammate_id: int
    title: str
    created_at: datetime
    updated_at: datetime

class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []
    execution_events: list["ExecutionEventOut"] = []

class ExecutionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str | None
    tool_call_id: str | None
    event_type: str
    status: str
    tool_name: str | None
    payload: dict[str, Any]
    created_at: datetime

class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)

class HealthOut(BaseModel):
    status: str
    provider: str

class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]


class TelegramConfigureRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    chat_allowlist: list[int] = Field(default_factory=list)

    def sanitized_allowlist(self) -> list[int]:
        return sorted({int(x) for x in self.chat_allowlist})


class ConnectorStatusOut(BaseModel):
    id: str
    display_name: str
    connected: bool
    healthy: bool
    credentials_writable: bool
    bot_username: str | None = None
    bot_id: int | None = None
    chat_allowlist: list[int] = []
    capabilities: list[dict[str, Any]] = []
    message: str = ""
    token_configured: bool = False


class AutomationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=20_000)
    teammate_id: int
    schedule_kind: str = "manual"
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str | None = None
    enabled: bool = True
    paused: bool = False
    missed_policy: str = "skip_catch_up"
    include_previous_summary: bool = False
    autonomy_policy: str = "ask"
    max_iterations: int | None = None
    max_tool_calls: int | None = None
    max_run_seconds: float | None = None
    run_on_startup: bool = False


class AutomationUpdate(BaseModel):
    name: str | None = None
    instruction: str | None = None
    schedule_kind: str | None = None
    schedule: dict[str, Any] | None = None
    timezone: str | None = None
    enabled: bool | None = None
    paused: bool | None = None
    missed_policy: str | None = None
    include_previous_summary: bool | None = None
    autonomy_policy: str | None = None
    max_iterations: int | None = None
    max_tool_calls: int | None = None
    max_run_seconds: float | None = None
    run_on_startup: bool | None = None


class AutomationOut(BaseModel):
    id: str
    name: str
    instruction: str
    teammate_id: int
    enabled: bool
    paused: bool
    timezone: str
    schedule_kind: str
    schedule: dict[str, Any]
    next_run_at: datetime | None
    missed_policy: str
    include_previous_summary: bool
    autonomy_policy: str
    max_iterations: int
    max_tool_calls: int
    max_run_seconds: float
    run_on_startup: bool
    created_at: datetime
    updated_at: datetime


class AutomationRunOut(BaseModel):
    id: str
    automation_id: str
    conversation_id: int | None
    agent_run_id: str | None
    status: str
    trigger_type: str
    occurrence_key: str | None
    instruction_snapshot: str
    progress: dict[str, Any]
    checkpoint: dict[str, Any]
    error_message: str | None
    stop_reason: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AutomationRunReceipt(BaseModel):
    run: AutomationRunOut
    events: list[ExecutionEventOut]
    assistant_text: str
    checkpoint: dict[str, Any]
    progress: dict[str, Any]


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    automation_run_id: str | None
    read: bool
    created_at: datetime


class WebhookCreateOut(BaseModel):
    id: str
    automation_id: str
    name: str
    secret: str
    secret_prefix: str
    path: str
    message: str


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4_000)
    memory_type: Literal["preference", "teammate", "project", "task", "fact"] = "preference"
    scope: Literal["user", "teammate", "project", "task"] = "user"
    scope_id: str = Field(default="default", max_length=120)
    source: Literal["explicit", "inferred"] = "explicit"
    tags: list[str] = Field(default_factory=list, max_length=10)
    source_conversation_id: int | None = None
    source_run_id: str | None = None
    supersedes_id: str | None = None


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=4_000)


class MemoryOut(BaseModel):
    id: str
    memory_type: str
    scope: str
    scope_id: str
    content: str
    source: str
    status: str
    tags: list[str] = []
    source_conversation_id: int | None = None
    source_run_id: str | None = None
    supersedes_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillInstallOut(BaseModel):
    id: str
    skill_id: str
    version: str
    name: str
    enabled: bool
    teammate_id: int | None
    package_path: str
    allowed_tool_categories: list[str] = []
    reviewed: bool
    created_at: datetime
    updated_at: datetime


class SkillInstallCreate(BaseModel):
    skill_id: str = Field(min_length=2, max_length=80)
    teammate_id: int | None = None
    reviewed: bool = False
    enabled: bool = False


class SkillEnableIn(BaseModel):
    enabled: bool


class ConnectorGrantIn(BaseModel):
    teammate_id: int
    connector_id: str = Field(min_length=1, max_length=64)
    operations: list[str] = Field(default_factory=list)
    resource_scope: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ConnectorGrantOut(BaseModel):
    id: str
    teammate_id: int
    connector_id: str
    operations: list[str]
    resource_scope: dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class WorkflowStepIn(BaseModel):
    step_key: str = Field(min_length=1, max_length=80)
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    tool_name: str | None = None


class WorkflowCreate(BaseModel):
    teammate_id: int
    title: str = Field(default="Workflow", max_length=200)
    instruction: str = Field(default="", max_length=20_000)
    skill_id: str | None = None
    conversation_id: int | None = None
    steps: list[WorkflowStepIn] | None = None
    project_id: str | None = None


class WorkflowOut(BaseModel):
    id: str
    teammate_id: int
    conversation_id: int | None
    title: str
    instruction: str
    skill_id: str | None
    skill_version: str | None
    status: str
    memory_ids: list[str] = []
    report: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
