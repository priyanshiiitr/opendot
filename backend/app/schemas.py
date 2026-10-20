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
