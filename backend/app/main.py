import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from .automations import AutomationService, AutomationWorker
from .automations.service import AutomationError
from .automations.webhooks import build_webhook_router
from .browser import NavigationPolicy
from .browser.tool_handlers import resolve_teammate_browser
from .computers import ComputerError, ComputerManager
from .config import get_settings
from .connectors import ConnectorManager, InMemoryCredentialStore, build_credential_store
from .database import ConnectorGrant, Conversation, Message, SessionLocal, Teammate, get_db, init_db
from .memory import MemoryError, MemoryService
from .memory.tools import register_memory_tools
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
    ComputerProvisionRequest,
    ComputerStatusOut,
    ConnectorGrantIn,
    ConnectorGrantOut,
    ConnectorStatusOut,
    ConversationDetail,
    ConversationOut,
    ExecutionEventOut,
    HealthOut,
    MemoryCreate,
    MemoryOut,
    MemoryUpdate,
    MessageOut,
    NotificationOut,
    SkillEnableIn,
    SkillInstallCreate,
    DeviceOut,
    DiagnosticsOut,
    PairingCompleteIn,
    PairingCompleteOut,
    PairingStartOut,
    RemoteStatusOut,
    SkillInstallOut,
    TeamCreate,
    TeamOut,
    TeamRunCreate,
    TeamRunOut,
    TeamUpdate,
    TeammateCreate,
    TeammateOut,
    TelegramConfigureRequest,
    WebhookCreateOut,
    WorkflowCreate,
    WorkflowOut,
)
from .remote import RemoteAccessError, RemoteAccessService
from .remote.access import authorize_request, client_ip, is_loopback
from .skills import SkillError, SkillRegistry
from .teams import TeamCoordinator, TeamError, TeamService
from .tools import ToolRegistry
from .workflows import WorkflowError, WorkflowService

settings = get_settings()
navigation_policy = NavigationPolicy(
    fixture_mode=settings.browser_fixture_mode,
    fixture_host=settings.browser_fixture_host,
    fixture_port=settings.browser_fixture_port,
)
computer_manager = ComputerManager(settings, SessionLocal, navigation_policy)
memory_service = MemoryService()
skill_registry = SkillRegistry(Path(settings.skills_root_dir))
workflow_service = WorkflowService()
registry = ToolRegistry(
    settings.agent_default_timezone,
    settings.agent_tool_timeout_seconds,
    settings.agent_max_argument_bytes,
    settings.agent_max_result_bytes,
    browser=None,
    browser_timeout_seconds=settings.browser_tool_timeout_seconds,
    resolve_browser=resolve_teammate_browser(computer_manager) if settings.browser_enabled else None,
    computer_manager=computer_manager,
)
register_memory_tools(registry, SessionLocal, memory_service, settings.agent_tool_timeout_seconds)

# Ensure schema (including connectors) exists before connector status lookups on import/tests.
init_db()
skill_registry.ensure_builtin_seeded()

# Tests may replace credential_store / connector_manager before requests.
credential_store = build_credential_store()
connector_manager = ConnectorManager(credential_store, SessionLocal, registry)
runtime = AgentRuntime(settings, registry, PermissionBroker(), computer_manager, memory_service)
automation_service = AutomationService(settings)
automation_worker = AutomationWorker(settings, SessionLocal, runtime, automation_service)
team_service = TeamService(settings)
team_coordinator = TeamCoordinator(settings, SessionLocal, runtime, team_service)
remote_access = RemoteAccessService(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    skill_registry.ensure_builtin_seeded()
    computer_manager.reconcile_on_startup()
    with SessionLocal() as db:
        team_coordinator.reconcile_on_startup(db)
    connector_manager.refresh_tools()
    await automation_worker.start()
    try:
        yield
    finally:
        await automation_worker.stop()
        await computer_manager.shutdown()


app = FastAPI(title=settings.app_name, version="0.9.0", lifespan=lifespan)
# CORS: explicit origin allowlist only — never "*" with credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-AI-Teammates-Session"],
)


class RemoteAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        with SessionLocal() as db:
            try:
                session = authorize_request(request, db, settings, remote_access)
                request.state.device_session = session
            except HTTPException as exc:
                return Response(
                    content=json.dumps({"detail": exc.detail}),
                    status_code=exc.status_code,
                    media_type="application/json",
                )
        return await call_next(request)


app.add_middleware(RemoteAuthMiddleware)
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


@app.get("/ready")
def ready() -> dict:
    """Readiness: process up and DB reachable. Does not imply browsers are open."""
    try:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        worker_ok = (not settings.automation_worker_enabled) or (automation_worker._task is not None and not automation_worker._task.done())
        return {
            "ready": True,
            "database": True,
            "worker_enabled": settings.automation_worker_enabled,
            "worker_running": worker_ok,
            "remote_access_enabled": settings.remote_access_enabled,
            "bind_host": settings.bind_host,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Not ready: {exc}") from exc


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


@app.post("/api/teammates", response_model=TeammateOut)
def create_teammate(request: TeammateCreate, db: Session = Depends(get_db)) -> Teammate:
    teammate = Teammate(
        name=request.name.strip(),
        description=request.description.strip(),
        system_prompt=request.system_prompt.strip(),
        role_label=request.role_label.strip(),
        enabled=request.enabled,
    )
    db.add(teammate)
    db.commit()
    db.refresh(teammate)
    computer_manager.get_or_create_record(db, teammate.id)
    return teammate


@app.get("/api/teammates/{teammate_id}/computer", response_model=ComputerStatusOut)
def get_computer(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        # Do not recreate after delete — provision/start create explicitly.
        return computer_manager.public_status(db, teammate_id, create=False)
    except ComputerError as exc:
        raise HTTPException(404 if "not found" in exc.reason.lower() else 400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/provision", response_model=ComputerStatusOut)
async def provision_computer(
    teammate_id: int,
    body: ComputerProvisionRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    provider = body.provider if body else None
    confirm_estimate = bool(body.confirm_estimate) if body else False
    try:
        return await computer_manager.provision(
            teammate_id, provider=provider, confirm_estimate=confirm_estimate
        )
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.get("/api/teammates/{teammate_id}/computer/health")
async def computer_health(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.health(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/delete")
async def delete_computer(
    teammate_id: int,
    confirm: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.delete_computer(teammate_id, confirm=confirm)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/start", response_model=ComputerStatusOut)
async def start_computer(teammate_id: int, headed: bool = False, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.start(teammate_id, headed=headed)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/stop", response_model=ComputerStatusOut)
async def stop_computer(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.stop(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/pause", response_model=ComputerStatusOut)
async def pause_computer(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.pause(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/resume", response_model=ComputerStatusOut)
async def resume_computer(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.resume(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/takeover", response_model=ComputerStatusOut)
async def takeover_computer(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.takeover(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/release", response_model=ComputerStatusOut)
async def release_computer(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.release_control(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/computer/clear-browser", response_model=ComputerStatusOut)
async def clear_computer_browser(teammate_id: int, confirm: bool = False, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return await computer_manager.clear_browser_data(teammate_id, confirm=confirm)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.get("/api/teammates/{teammate_id}/computer/preview")
async def computer_preview(teammate_id: int, db: Session = Depends(get_db)) -> Response:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        data = await computer_manager.preview(teammate_id)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


async def _gcp_computer_from_agent(computer_id: str, request: Request):
    from .computers.gcp_compute import GcpComputeComputer

    token = request.headers.get("X-Computer-Agent-Token") or ""
    if not token:
        raise HTTPException(401, "Missing agent token")
    try:
        live = await computer_manager.ensure_live(computer_id)
    except ComputerError as exc:
        raise HTTPException(404, exc.reason) from exc
    if not isinstance(live, GcpComputeComputer):
        raise HTTPException(400, "Computer is not a gcp_compute instance")
    if not live.verify_agent_token(token):
        raise HTTPException(403, "Invalid agent token")
    return live


@app.post("/api/internal/computers/{computer_id}/agent/heartbeat")
async def agent_heartbeat(computer_id: str, request: Request) -> dict:
    live = await _gcp_computer_from_agent(computer_id, request)
    live.record_heartbeat()
    return {"ok": True}


@app.get("/api/internal/computers/{computer_id}/agent/commands")
async def agent_commands(computer_id: str, request: Request) -> dict:
    live = await _gcp_computer_from_agent(computer_id, request)
    return {"commands": live.drain_commands()}


@app.post("/api/internal/computers/{computer_id}/agent/preview")
async def agent_preview_upload(computer_id: str, request: Request) -> dict:
    import base64

    live = await _gcp_computer_from_agent(computer_id, request)
    body = await request.json()
    png_b64 = body.get("png_b64") or ""
    try:
        data = base64.b64decode(png_b64)
    except Exception as exc:
        raise HTTPException(400, "Invalid png_b64") from exc
    if len(data) > settings.computer_max_file_bytes:
        raise HTTPException(400, "Preview too large")
    live.store_preview_bytes(data)
    return {"ok": True, "bytes": len(data)}


@app.get("/api/teammates/{teammate_id}/workspace")
def list_workspace(teammate_id: int, area: str = Query(default="workspace"), db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    if area not in {"workspace", "artifacts"}:
        raise HTTPException(400, "area must be workspace or artifacts")
    try:
        return {"area": area, "files": computer_manager.list_workspace(teammate_id, area=area)}
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.get("/api/teammates/{teammate_id}/workspace/file")
def read_workspace_file(
    teammate_id: int,
    path: str = Query(min_length=1),
    area: str = Query(default="workspace"),
    db: Session = Depends(get_db),
) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        return computer_manager.read_workspace_file(teammate_id, path, area=area)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.get("/api/teammates/{teammate_id}/workspace/download")
def download_workspace_file(
    teammate_id: int,
    path: str = Query(min_length=1),
    area: str = Query(default="artifacts"),
    db: Session = Depends(get_db),
) -> Response:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    try:
        abs_path, data = computer_manager.file_bytes(teammate_id, path, area=area)
    except ComputerError as exc:
        raise HTTPException(400, exc.reason) from exc
    media = "application/octet-stream"
    if abs_path.suffix.lower() == ".png":
        media = "image/png"
    elif abs_path.suffix.lower() in {".md", ".txt", ".csv", ".json", ".html"}:
        media = "text/plain; charset=utf-8"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{abs_path.name}"', "Cache-Control": "no-store"},
    )


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
            "link_ref": getattr(n, "link_ref", None),
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
        "link_ref": getattr(n, "link_ref", None),
        "read": n.read,
        "created_at": n.created_at,
    }


# --- Milestone 9: remote companion / always-on ops ---


@app.get("/api/remote/status", response_model=RemoteStatusOut)
def remote_status() -> dict:
    enabled = settings.remote_access_enabled
    return {
        "remote_access_enabled": enabled,
        "bind_host": settings.bind_host,
        "bind_port": settings.bind_port,
        "local_trust_loopback": settings.local_trust_loopback,
        "pairing_available": enabled,
        "message": (
            "Remote access enabled. Pair devices with a short-lived code. Private network (e.g. Tailscale) recommended."
            if enabled
            else "Remote access disabled (default). Backend remains loopback-oriented; enable explicitly to pair devices."
        ),
    }


@app.post("/api/remote/pairing/start", response_model=PairingStartOut)
def pairing_start(request: Request, db: Session = Depends(get_db)) -> dict:
    if not is_loopback(request):
        raise HTTPException(403, "Pairing must be started from the trusted local desktop (loopback).")
    try:
        return remote_access.start_pairing(db, client_key=client_ip(request))
    except RemoteAccessError as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc


@app.post("/api/remote/pairing/complete", response_model=PairingCompleteOut)
def pairing_complete(body: PairingCompleteIn, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        return remote_access.complete_pairing(
            db, code=body.code, device_name=body.device_name, client_key=client_ip(request)
        )
    except RemoteAccessError as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc


@app.get("/api/remote/devices", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db)) -> list[dict]:
    return remote_access.list_devices(db)


@app.delete("/api/remote/devices/{session_id}")
def revoke_device(session_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        remote_access.revoke_device(db, session_id)
    except RemoteAccessError as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc
    return {"revoked": True}


@app.post("/api/remote/logout")
def remote_logout(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    session = getattr(request.state, "device_session", None)
    if session is None:
        # Also accept bearer for logout when loopback trust skipped session attachment
        from .remote.access import extract_bearer

        session = remote_access.resolve_session(db, extract_bearer(request))
    if session is None:
        raise HTTPException(401, "No active session.")
    remote_access.logout(db, session)
    return {"logged_out": True}


@app.get("/api/diagnostics", response_model=DiagnosticsOut)
def diagnostics(db: Session = Depends(get_db)) -> dict:
    from .database import AutomationRun, ComputerRecord, DeviceSession, ExecutionEvent, SecurityAuditEvent

    waiting = (
        db.query(ExecutionEvent)
        .filter(ExecutionEvent.event_type == "approval", ExecutionEvent.status == "waiting_approval")
        .count()
    )
    active = db.query(AutomationRun).filter(AutomationRun.status.in_(["queued", "running", "waiting_approval"])).count()
    devices = db.query(DeviceSession).filter(DeviceSession.revoked.is_(False)).count()
    computers = db.query(ComputerRecord).filter(ComputerRecord.state.in_(["ready", "busy", "waiting_human"])).count()
    events = (
        db.query(SecurityAuditEvent).order_by(SecurityAuditEvent.created_at.desc()).limit(15).all()
    )
    worker_running = (not settings.automation_worker_enabled) or (
        automation_worker._task is not None and not automation_worker._task.done()
    )
    notes = [
        "Closing the Electron window does not stop this backend process.",
        "Jobs do not run when the host PC is powered off.",
        "A DB computer row does not prove the browser process is alive.",
        "Remote access defaults to disabled; private VPN (e.g. Tailscale) is the documented path when enabled.",
    ]
    if settings.remote_access_enabled and settings.bind_host == "127.0.0.1":
        notes.append("Remote access is enabled but bind_host is still 127.0.0.1 — companions on other devices cannot reach it until you bind a private interface intentionally.")
    return {
        "status": "ok",
        "provider": settings.model_provider,
        "remote_access_enabled": settings.remote_access_enabled,
        "worker_enabled": settings.automation_worker_enabled,
        "worker_running": bool(worker_running),
        "bind_host": settings.bind_host,
        "active_automation_runs": active,
        "waiting_approvals": waiting,
        "paired_devices": devices,
        "browser_computers_ready": computers,
        "recent_security_events": [
            {"id": e.id, "event_type": e.event_type, "detail": e.detail, "created_at": e.created_at}
            for e in events
        ],
        "notes": notes,
    }


# --- Milestone 7: memory, skills, grants, workflows ---


@app.get("/api/memories", response_model=list[MemoryOut])
def list_memories(
    scope: str | None = None,
    scope_id: str | None = None,
    memory_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = memory_service.list(db, scope=scope, scope_id=scope_id, memory_type=memory_type)
    return [MemoryService.public(r) for r in rows]


@app.post("/api/memories", response_model=MemoryOut)
def create_memory(request: MemoryCreate, db: Session = Depends(get_db)) -> dict:
    try:
        row = memory_service.create(db, **request.model_dump())
    except MemoryError as exc:
        raise HTTPException(400, exc.reason) from exc
    return MemoryService.public(row)


@app.get("/api/memories/retrieve/for-task", response_model=list[MemoryOut])
def retrieve_memories_for_task(
    teammate_id: int,
    query: str = "",
    project_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    rows = memory_service.retrieve_for_task(
        db, teammate_id=teammate_id, project_id=project_id, query=query, limit=settings.memory_max_retrieve
    )
    return [MemoryService.public(r) for r in rows]


@app.get("/api/memories/{memory_id}", response_model=MemoryOut)
def get_memory(memory_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return MemoryService.public(memory_service.get(db, memory_id))
    except MemoryError as exc:
        raise HTTPException(404, exc.reason) from exc


@app.patch("/api/memories/{memory_id}", response_model=MemoryOut)
def update_memory(memory_id: str, request: MemoryUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        return MemoryService.public(memory_service.update(db, memory_id, request.content))
    except MemoryError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.delete("/api/memories/{memory_id}", response_model=MemoryOut)
def delete_memory(memory_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return MemoryService.public(memory_service.delete(db, memory_id))
    except MemoryError as exc:
        raise HTTPException(404, exc.reason) from exc


@app.post("/api/teammates/{teammate_id}/memories/clear")
def clear_teammate_memories(teammate_id: int, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, teammate_id):
        raise HTTPException(404, "Teammate not found")
    rows = memory_service.list(db, scope="teammate", scope_id=str(teammate_id), limit=500)
    for row in rows:
        memory_service.delete(db, row.id)
    return {"cleared": len(rows), "teammate_id": teammate_id}


@app.get("/api/skills/catalog")
def skills_catalog() -> list[dict]:
    return [SkillRegistry.public_package(p) for p in skill_registry.catalog()]


@app.get("/api/skills/installs", response_model=list[SkillInstallOut])
def list_skill_installs(teammate_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return [SkillRegistry.public_install(r) for r in skill_registry.list_installs(db, teammate_id)]


@app.post("/api/skills/installs", response_model=SkillInstallOut)
def install_skill(request: SkillInstallCreate, db: Session = Depends(get_db)) -> dict:
    try:
        package = skill_registry.get_package(request.skill_id)
        row = skill_registry.install(
            db,
            package,
            teammate_id=request.teammate_id,
            reviewed=request.reviewed,
            enabled=request.enabled,
        )
    except SkillError as exc:
        raise HTTPException(400, exc.reason) from exc
    return SkillRegistry.public_install(row)


@app.post("/api/skills/installs/{install_id}/review", response_model=SkillInstallOut)
def review_skill_install(install_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return SkillRegistry.public_install(skill_registry.mark_reviewed(db, install_id))
    except SkillError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/skills/installs/{install_id}/enable", response_model=SkillInstallOut)
def enable_skill_install(install_id: str, request: SkillEnableIn, db: Session = Depends(get_db)) -> dict:
    try:
        return SkillRegistry.public_install(skill_registry.set_enabled(db, install_id, request.enabled))
    except SkillError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.get("/api/skills/{skill_id}")
def get_skill_package(skill_id: str) -> dict:
    try:
        return SkillRegistry.public_package(skill_registry.get_package(skill_id))
    except SkillError as exc:
        raise HTTPException(404, exc.reason) from exc


@app.get("/api/connector-grants", response_model=list[ConnectorGrantOut])
def list_connector_grants(teammate_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(ConnectorGrant)
    if teammate_id is not None:
        q = q.filter(ConnectorGrant.teammate_id == teammate_id)
    rows = q.order_by(ConnectorGrant.updated_at.desc()).all()
    return [
        {
            "id": g.id,
            "teammate_id": g.teammate_id,
            "connector_id": g.connector_id,
            "operations": json.loads(g.operations_json or "[]"),
            "resource_scope": json.loads(g.resource_scope_json or "{}"),
            "enabled": g.enabled,
            "created_at": g.created_at,
            "updated_at": g.updated_at,
        }
        for g in rows
    ]


@app.post("/api/connector-grants", response_model=ConnectorGrantOut)
def upsert_connector_grant(request: ConnectorGrantIn, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, request.teammate_id):
        raise HTTPException(404, "Teammate not found")
    existing = (
        db.query(ConnectorGrant)
        .filter(
            ConnectorGrant.teammate_id == request.teammate_id,
            ConnectorGrant.connector_id == request.connector_id,
        )
        .first()
    )
    if existing:
        existing.operations_json = json.dumps(request.operations)
        existing.resource_scope_json = json.dumps(request.resource_scope)
        existing.enabled = request.enabled
        db.commit()
        db.refresh(existing)
        g = existing
    else:
        g = ConnectorGrant(
            id=str(uuid.uuid4()),
            teammate_id=request.teammate_id,
            connector_id=request.connector_id,
            operations_json=json.dumps(request.operations),
            resource_scope_json=json.dumps(request.resource_scope),
            enabled=request.enabled,
        )
        db.add(g)
        db.commit()
        db.refresh(g)
    return {
        "id": g.id,
        "teammate_id": g.teammate_id,
        "connector_id": g.connector_id,
        "operations": json.loads(g.operations_json or "[]"),
        "resource_scope": json.loads(g.resource_scope_json or "{}"),
        "enabled": g.enabled,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }


@app.delete("/api/connector-grants/{grant_id}")
def revoke_connector_grant(grant_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    g = db.get(ConnectorGrant, grant_id)
    if not g:
        raise HTTPException(404, "Grant not found")
    g.enabled = False
    db.commit()
    return {"revoked": True}


@app.get("/api/workflows", response_model=list[WorkflowOut])
def list_workflows(teammate_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    from .database import WorkflowRun

    q = db.query(WorkflowRun)
    if teammate_id is not None:
        q = q.filter(WorkflowRun.teammate_id == teammate_id)
    rows = q.order_by(WorkflowRun.created_at.desc()).limit(50).all()
    return [workflow_service.public(db, w) for w in rows]


@app.post("/api/workflows", response_model=WorkflowOut)
def create_workflow(request: WorkflowCreate, db: Session = Depends(get_db)) -> dict:
    if not db.get(Teammate, request.teammate_id):
        raise HTTPException(404, "Teammate not found")
    skill_pkg = None
    skill_version = None
    skill_id = request.skill_id
    steps = [s.model_dump() for s in (request.steps or [])]
    try:
        if skill_id:
            _install, skill_pkg = skill_registry.resolve_enabled(db, skill_id, request.teammate_id)
            skill_version = skill_pkg.version
            if not steps:
                steps = workflow_service.plan_from_skill(skill_pkg, request.instruction)
        memories = memory_service.retrieve_for_task(
            db,
            teammate_id=request.teammate_id,
            project_id=request.project_id,
            query=request.instruction,
            limit=settings.memory_max_retrieve,
        )
        memory_ids = [m.id for m in memories]
        run = workflow_service.create(
            db,
            teammate_id=request.teammate_id,
            title=request.title,
            instruction=request.instruction,
            steps=steps,
            skill_id=skill_id,
            skill_version=skill_version,
            memory_ids=memory_ids,
            conversation_id=request.conversation_id,
        )
    except (SkillError, WorkflowError) as exc:
        raise HTTPException(400, getattr(exc, "reason", str(exc))) from exc
    return workflow_service.public(db, run)


@app.get("/api/workflows/{workflow_id}", response_model=WorkflowOut)
def get_workflow(workflow_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return workflow_service.public(db, workflow_service.get(db, workflow_id))
    except WorkflowError as exc:
        raise HTTPException(404, exc.reason) from exc


@app.post("/api/workflows/{workflow_id}/advance", response_model=WorkflowOut)
def advance_workflow(
    workflow_id: str,
    complete_execute: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Advance one workflow step. Skill category gates are applied only for this call."""
    try:
        wf = workflow_service.get(db, workflow_id)
        skill_pkg = None
        workspace_root = None
        if wf.skill_id:
            try:
                _, skill_pkg = skill_registry.resolve_enabled(db, wf.skill_id, wf.teammate_id)
            except SkillError:
                skill_pkg = None
        try:
            store = computer_manager.workspace_for_teammate(wf.teammate_id)
            workspace_root = store.workspace
        except Exception:  # noqa: BLE001
            workspace_root = None
        previous_categories = runtime._skill_categories
        try:
            if skill_pkg is not None:
                runtime.set_skill_categories(list(skill_pkg.allowed_tool_categories))
            return workflow_service.advance_deterministic(
                db,
                workflow_id,
                workspace_root=workspace_root,
                skill=skill_pkg,
                complete_execute=complete_execute,
            )
        finally:
            # Never leave skill gates on the shared AgentRuntime (breaks other chats/tests).
            runtime.set_skill_categories(previous_categories)
    except WorkflowError as exc:
        raise HTTPException(400, exc.reason) from exc


# --- Milestone 8: multi-agent teams ---


@app.get("/api/teams", response_model=list[TeamOut])
def list_teams(db: Session = Depends(get_db)) -> list[dict]:
    return [team_service.public_team(t) for t in team_service.list_teams(db)]


@app.post("/api/teams", response_model=TeamOut)
def create_team(request: TeamCreate, db: Session = Depends(get_db)) -> dict:
    try:
        team = team_service.create_team(db, request.model_dump())
    except TeamError as exc:
        raise HTTPException(400, exc.reason) from exc
    return team_service.public_team(team)


@app.get("/api/teams/{team_id}", response_model=TeamOut)
def get_team(team_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return team_service.public_team(team_service.get_team(db, team_id))
    except TeamError as exc:
        raise HTTPException(404, exc.reason) from exc


@app.patch("/api/teams/{team_id}", response_model=TeamOut)
def patch_team(team_id: str, request: TeamUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        team = team_service.update_team(db, team_id, request.model_dump(exclude_unset=True))
    except TeamError as exc:
        raise HTTPException(400, exc.reason) from exc
    return team_service.public_team(team)


@app.get("/api/team-runs", response_model=list[TeamRunOut])
def list_team_runs(team_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return [team_service.public_run(db, r) for r in team_service.list_runs(db, team_id)]


@app.post("/api/teams/{team_id}/runs", response_model=TeamRunOut)
def create_team_run(team_id: str, request: TeamRunCreate, db: Session = Depends(get_db)) -> dict:
    try:
        run = team_service.create_run(
            db,
            team_id=team_id,
            objective=request.objective,
            subtasks=[s.model_dump() for s in request.subtasks],
        )
    except TeamError as exc:
        raise HTTPException(400, exc.reason) from exc
    return team_service.public_run(db, run)


@app.get("/api/team-runs/{run_id}", response_model=TeamRunOut)
def get_team_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return team_service.public_run(db, team_service.get_run(db, run_id))
    except TeamError as exc:
        raise HTTPException(404, exc.reason) from exc


@app.post("/api/team-runs/{run_id}/tick", response_model=TeamRunOut)
async def tick_team_run(run_id: str, max_steps: int | None = None) -> dict:
    try:
        return await team_coordinator.tick(run_id, max_steps=max_steps)
    except TeamError as exc:
        raise HTTPException(400, exc.reason) from exc


@app.post("/api/team-runs/{run_id}/cancel", response_model=TeamRunOut)
def cancel_team_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return team_service.public_run(db, team_service.cancel_run(db, run_id))
    except TeamError as exc:
        raise HTTPException(404, exc.reason) from exc


def use_test_connectors(store: InMemoryCredentialStore | None = None) -> ConnectorManager:
    """Test helper: swap in an in-memory credential store and refresh tools."""
    global credential_store, connector_manager
    credential_store = store or InMemoryCredentialStore()
    connector_manager = ConnectorManager(credential_store, SessionLocal, registry)
    return connector_manager
