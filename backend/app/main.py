import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .automations import AutomationService, AutomationWorker
from .automations.service import AutomationError
from .automations.webhooks import build_webhook_router
from .browser import BrowserService, NavigationPolicy
from .config import get_settings
from .connectors import ConnectorManager, InMemoryCredentialStore, build_credential_store
from .database import Conversation, Message, SessionLocal, Teammate, get_db, init_db
from .permissions import PermissionBroker
from .providers import build_provider
from .runtime import AgentRuntime
from .schemas import (
    ApprovalDecision,
    AutomationCreate,
    AutomationOut,
    AutomationRunOut,
    AutomationRunReceipt,
    AutomationUpdate,
    ChatRequest,
    ConnectorStatusOut,
    ConversationDetail,
    ConversationOut,
    ExecutionEventOut,
    HealthOut,
    MessageOut,
    NotificationOut,
    TeammateOut,
    TelegramConfigureRequest,
    WebhookCreateOut,
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

# Ensure schema (including connectors) exists before connector status lookups on import/tests.
init_db()

# Tests may replace credential_store / connector_manager before requests.
credential_store = build_credential_store()
connector_manager = ConnectorManager(credential_store, SessionLocal, registry)
runtime = AgentRuntime(settings, registry, PermissionBroker())
automation_service = AutomationService(settings)
automation_worker = AutomationWorker(settings, SessionLocal, runtime, automation_service)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    connector_manager.refresh_tools()
    await automation_worker.start()
    try:
        yield
    finally:
        await automation_worker.stop()
        await browser_service.close()


app = FastAPI(title=settings.app_name, version="0.5.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(build_webhook_router(settings, automation_service))


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


@app.get("/api/connectors", response_model=list[ConnectorStatusOut])
def list_connectors() -> list[dict]:
    return connector_manager.list_statuses()


@app.get("/api/connectors/telegram", response_model=ConnectorStatusOut)
def telegram_status() -> dict:
    return connector_manager.status("telegram").public_dict()


@app.post("/api/connectors/telegram/configure", response_model=ConnectorStatusOut)
async def telegram_configure(request: TelegramConfigureRequest) -> dict:
    try:
        return await connector_manager.configure_telegram(request.token, request.sanitized_allowlist())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, "Could not configure Telegram connector.") from exc


@app.post("/api/connectors/telegram/verify", response_model=ConnectorStatusOut)
async def telegram_verify() -> dict:
    return await connector_manager.verify_telegram()


@app.post("/api/connectors/telegram/disconnect", response_model=ConnectorStatusOut)
async def telegram_disconnect() -> dict:
    return await connector_manager.disconnect_telegram()


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


@app.get("/api/automations", response_model=list[AutomationOut])
def list_automations(db: Session = Depends(get_db)) -> list[dict]:
    return [automation_service.public_automation(a) for a in automation_service.list_automations(db)]


@app.post("/api/automations", response_model=AutomationOut)
def create_automation(request: AutomationCreate, db: Session = Depends(get_db)) -> dict:
    try:
        auto = automation_service.create(db, request.model_dump())
    except AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return automation_service.public_automation(auto)


@app.get("/api/automations/{automation_id}", response_model=AutomationOut)
def get_automation(automation_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return automation_service.public_automation(automation_service.get(db, automation_id))
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/api/automations/{automation_id}", response_model=AutomationOut)
def patch_automation(automation_id: str, request: AutomationUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        auto = automation_service.update(db, automation_id, request.model_dump(exclude_unset=True))
    except AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return automation_service.public_automation(auto)


@app.delete("/api/automations/{automation_id}")
def delete_automation(automation_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        automation_service.delete(db, automation_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": True}


@app.post("/api/automations/{automation_id}/run", response_model=AutomationRunOut)
def run_automation_now(automation_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        run = automation_service.run_now(db, automation_id)
    except AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return automation_service.public_run(run)


@app.get("/api/automations/{automation_id}/runs", response_model=list[AutomationRunOut])
def list_automation_runs(automation_id: str, db: Session = Depends(get_db)) -> list[dict]:
    try:
        automation_service.get(db, automation_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [automation_service.public_run(r) for r in automation_service.list_runs(db, automation_id)]


@app.get("/api/automation-runs", response_model=list[AutomationRunOut])
def list_all_automation_runs(db: Session = Depends(get_db)) -> list[dict]:
    return [automation_service.public_run(r) for r in automation_service.list_runs(db)]


@app.get("/api/automation-runs/{run_id}", response_model=AutomationRunReceipt)
def get_automation_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        receipt = automation_service.run_receipt(db, run_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "run": automation_service.public_run(receipt["run"]),
        "events": [
            ExecutionEventOut(
                id=e["id"],
                run_id=e["run_id"],
                tool_call_id=e["tool_call_id"],
                event_type=e["event_type"],
                status=e["status"],
                tool_name=e["tool_name"],
                payload=e["payload"],
                created_at=e["created_at"],
            )
            for e in receipt["events"]
        ],
        "assistant_text": receipt["assistant_text"],
        "checkpoint": receipt["checkpoint"],
        "progress": receipt["progress"],
    }


@app.post("/api/automation-runs/{run_id}/cancel", response_model=AutomationRunOut)
def cancel_automation_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        run = automation_service.cancel_run(db, run_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return automation_service.public_run(run)


@app.post("/api/automation-runs/{run_id}/approvals/{approval_id}")
async def automation_approval(
    run_id: str, approval_id: str, request: ApprovalDecision, db: Session = Depends(get_db)
) -> dict[str, str]:
    try:
        run = automation_service.get_run(db, run_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    if run.status != "waiting_approval":
        raise HTTPException(409, "Run is not waiting for approval.")
    # Resume in-process via the worker (does not require an open chat SSE).
    await automation_worker.resume_after_approval(run_id, approval_id, request.decision)
    refreshed = automation_service.get_run(db, run_id)
    return {"status": refreshed.status, "run_id": run_id}


@app.post("/api/automations/{automation_id}/webhooks", response_model=WebhookCreateOut)
def create_automation_webhook(automation_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        hook, secret = automation_service.create_webhook(db, automation_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "id": hook.id,
        "automation_id": hook.automation_id,
        "name": hook.name,
        "secret": secret,
        "secret_prefix": hook.secret_prefix,
        "path": f"/hooks/{hook.id}",
        "message": "Store this secret now. It is not shown again. Bind to loopback only.",
    }


@app.get("/api/notifications", response_model=list[NotificationOut])
def list_notifications(unread_only: bool = False, db: Session = Depends(get_db)) -> list[dict]:
    rows = automation_service.list_notifications(db, unread_only=unread_only)
    return [
        {
            "id": n.id,
            "kind": n.kind,
            "title": n.title,
            "body": n.body,
            "automation_run_id": n.automation_run_id,
            "read": n.read,
            "created_at": n.created_at,
        }
        for n in rows
    ]


@app.post("/api/notifications/{notification_id}/read", response_model=NotificationOut)
def read_notification(notification_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        n = automation_service.mark_notification_read(db, notification_id)
    except AutomationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "automation_run_id": n.automation_run_id,
        "read": n.read,
        "created_at": n.created_at,
    }


def use_test_connectors(store: InMemoryCredentialStore | None = None) -> ConnectorManager:
    """Test helper: swap in an in-memory credential store and refresh tools."""
    global credential_store, connector_manager
    credential_store = store or InMemoryCredentialStore()
    connector_manager = ConnectorManager(credential_store, SessionLocal, registry)
    return connector_manager
