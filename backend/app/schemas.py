from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class TeammateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str

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
