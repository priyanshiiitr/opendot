import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .browser import BrowserService, NavigationPolicy
from .config import get_settings
from .database import Conversation, Message, Teammate, get_db, init_db
from .permissions import PermissionBroker
from .providers import build_provider
from .runtime import AgentRuntime
from .schemas import (
    ApprovalDecision,
    ChatRequest,
    ConversationDetail,
    ConversationOut,
    ExecutionEventOut,
    HealthOut,
    MessageOut,
    TeammateOut,
)
from .tools import ToolRegistry

settings = get_settings()
browser_service = BrowserService(
    NavigationPolicy(
        fixture_mode=settings.browser_fixture_mode,
        fixture_host=settings.browser_fixture_host,
        fixture_port=settings.browser_fixture_port,
    ),
    headless=settings.browser_headless,
    navigation_timeout_ms=settings.browser_navigation_timeout_ms,
    max_actions=settings.browser_max_actions,
    max_extract_bytes=settings.browser_max_extract_bytes,
    artifact_dir=settings.browser_artifact_dir,
)
registry = ToolRegistry(
    settings.agent_default_timezone,
    settings.agent_tool_timeout_seconds,
    settings.agent_max_argument_bytes,
    settings.agent_max_result_bytes,
    browser=browser_service if settings.browser_enabled else None,
    browser_timeout_seconds=settings.browser_tool_timeout_seconds,
)
runtime = AgentRuntime(settings, registry, PermissionBroker())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        yield
    finally:
        await browser_service.close()


app = FastAPI(title=settings.app_name, version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["*"], allow_headers=["*"])


def conversation_out(conversation: Conversation) -> ConversationDetail:
    return ConversationDetail(
        id=conversation.id,
        teammate_id=conversation.teammate_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageOut.model_validate(message) for message in conversation.messages if message.role in {"user", "assistant"}],
        execution_events=[
            ExecutionEventOut(
                id=e.id,
                run_id=e.run_id,
                tool_call_id=e.tool_call_id,
                event_type=e.event_type,
                status=e.status,
                tool_name=e.tool_name,
                payload=json.loads(e.payload),
                created_at=e.created_at,
            )
            for e in conversation.execution_events
        ],
    )


def sse(events: AsyncIterator[dict]) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", provider=settings.model_provider)


@app.get("/api/teammates", response_model=list[TeammateOut])
def list_teammates(db: Session = Depends(get_db)) -> list[Teammate]:
    return db.query(Teammate).order_by(Teammate.id).all()


@app.get("/api/teammates/{teammate_id}/conversations", response_model=list[ConversationOut])
def list_conversations(teammate_id: int, db: Session = Depends(get_db)) -> list[Conversation]:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    return db.query(Conversation).filter_by(teammate_id=teammate_id).order_by(Conversation.updated_at.desc()).all()


@app.post("/api/teammates/{teammate_id}/conversations", response_model=ConversationDetail)
def create_conversation(teammate_id: int, db: Session = Depends(get_db)) -> ConversationDetail:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    conversation = Conversation(teammate_id=teammate_id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation_out(conversation)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int, db: Session = Depends(get_db)) -> ConversationDetail:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    return conversation_out(conversation)


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: int, request: ChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    db.add(Message(conversation_id=conversation.id, role="user", content=request.content))
    db.commit()
    run = runtime.start_run(db, conversation.id)

    async def events() -> AsyncIterator[dict]:
        yield {"type": "run_started", "run_id": run.id}
        async for event in runtime.run(db, conversation, build_provider(settings), run):
            yield event

    return sse(events())


@app.post("/api/conversations/{conversation_id}/approvals/{approval_id}")
async def decide_approval(conversation_id: int, approval_id: str, request: ApprovalDecision, db: Session = Depends(get_db)) -> StreamingResponse:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    return sse(runtime.resolve_approval(db, conversation, approval_id, request.decision, build_provider(settings)))


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    if not runtime.cancel(db, run_id):
        raise HTTPException(409, "Run is not active")
    return {"cancelled": True}
