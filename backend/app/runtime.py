import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.orm import Session

from .config import Settings
from .database import AgentRun, Conversation, ExecutionEvent, Message
from .permissions import PermissionBroker
from .providers import ModelProvider, ProviderError, ToolCall
from .tools import ToolError, ToolRegistry


class AgentRuntime:
    def __init__(self, settings: Settings, registry: ToolRegistry, permissions: PermissionBroker):
        self.settings, self.registry, self.permissions = settings, registry, permissions

    def start_run(self, db: Session, conversation_id: int) -> AgentRun:
        run = AgentRun(id=str(uuid.uuid4()), conversation_id=conversation_id, status="running")
        db.add(run); db.commit(); return run

    def _event(self, db: Session, conversation_id: int, run_id: str | None, event_type: str, status: str, tool_name: str | None = None, tool_call_id: str | None = None, payload: dict[str, Any] | None = None) -> ExecutionEvent:
        event = ExecutionEvent(id=str(uuid.uuid4()), conversation_id=conversation_id, run_id=run_id, event_type=event_type, status=status, tool_name=tool_name, tool_call_id=tool_call_id, payload=json.dumps(payload or {}, ensure_ascii=False))
        db.add(event); db.commit(); return event

    @staticmethod
    def public_event(event: ExecutionEvent) -> dict[str, Any]:
        return {"type": "execution", "id": event.id, "run_id": event.run_id, "event_type": event.event_type, "status": event.status, "tool_name": event.tool_name, "tool_call_id": event.tool_call_id, "payload": json.loads(event.payload)}

    def _history(self, db: Session, conversation: Conversation) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        messages = db.query(Message).filter_by(conversation_id=conversation.id).order_by(Message.created_at, Message.id).all()
        for message in messages:
            if message.role == "assistant_tool_calls":
                history.append({"role": "assistant", "content": "", "tool_calls": json.loads(message.content)})
            elif message.role == "tool":
                data = json.loads(message.content)
                history.append({"role": "tool", "tool_call_id": data["tool_call_id"], "content": json.dumps(data["result"], ensure_ascii=False)})
            elif message.role in {"user", "assistant"}:
                history.append({"role": message.role, "content": message.content})
        return history

    def _cancelled(self, db: Session, run_id: str) -> bool:
        run = db.get(AgentRun, run_id)
        return run is None or run.cancelled

    async def run(self, db: Session, conversation: Conversation, provider: ModelProvider, run: AgentRun) -> AsyncIterator[dict[str, Any]]:
        calls_used = 0
        for iteration in range(self.settings.agent_max_iterations):
            if self._cancelled(db, run.id):
                run.status = "cancelled"; db.commit()
                yield {"type": "error", "message": "Task cancelled before further tool execution."}; return
            yielded_text = False
            text_chunks: list[str] = []
            try:
                async for event in provider.stream_turn(self._history(db, conversation), conversation.teammate.system_prompt, self.registry.schemas()):
                    if event.type == "text":
                        yielded_text = True
                        text_chunks.append(event.content)
                        yield {"type": "token", "content": event.content}
                        continue
                    if not event.tool_calls:
                        continue
                    serialized_calls = [{"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}} for call in event.tool_calls]
                    db.add(Message(conversation_id=conversation.id, role="assistant_tool_calls", content=json.dumps(serialized_calls)))
                    db.commit()
                    for call in event.tool_calls:
                        calls_used += 1
                        if calls_used > self.settings.agent_max_tool_calls:
                            run.status = "failed"; db.commit()
                            yield {"type": "error", "message": "Agent tool-call limit reached."}; return
                        async for item in self._handle_call(db, conversation, run, call):
                            yield item
                            if item.get("type") == "approval_required":
                                run.status = "waiting_approval"; db.commit(); return
                    break
                else:
                    if yielded_text:
                        db.add(Message(conversation_id=conversation.id, role="assistant", content="".join(text_chunks)))
                        db.commit()
                        run.status = "completed"; db.commit(); yield {"type": "done", "run_id": run.id}; return
            except ProviderError as exc:
                run.status = "failed"; db.commit(); yield {"type": "error", "message": str(exc)}; return
        run.status = "failed"; db.commit()
        yield {"type": "error", "message": "Agent iteration limit reached."}

    async def _handle_call(self, db: Session, conversation: Conversation, run: AgentRun, call: ToolCall) -> AsyncIterator[dict[str, Any]]:
        requested = self._event(db, conversation.id, run.id, "tool_call", "queued", call.name, call.id, {"arguments": call.arguments})
        yield self.public_event(requested)
        try:
            tool, arguments, public_args = self.registry.validate(call.name, call.arguments)
        except ToolError as exc:
            failed = self._event(db, conversation.id, run.id, "tool_result", "failed", call.name, call.id, {"error": str(exc)})
            db.add(Message(conversation_id=conversation.id, role="tool", content=json.dumps({"tool_call_id": call.id, "result": {"error": str(exc)}}))); db.commit()
            yield self.public_event(failed); return
        requested.payload = json.dumps({"arguments": public_args}); db.commit()
        decision = self.permissions.assess(tool)
        if decision.status == "approval_required":
            waiting = self._event(db, conversation.id, run.id, "approval", "waiting_approval", tool.name, call.id, {"arguments": public_args, "reason": decision.reason, "request_event_id": requested.id})
            yield {"type": "approval_required", "approval_id": waiting.id, "run_id": run.id, "tool_name": tool.name, "arguments": public_args, "reason": decision.reason}; return
        if decision.status != "allowed":
            failed = self._event(db, conversation.id, run.id, "tool_result", "failed", tool.name, call.id, {"error": decision.reason})
            db.add(Message(conversation_id=conversation.id, role="tool", content=json.dumps({"tool_call_id": call.id, "result": {"error": decision.reason}}))); db.commit()
            yield self.public_event(failed); return
        async for event in self._execute_validated(db, conversation, run, call, tool, arguments, public_args):
            yield event

    async def _execute_validated(self, db: Session, conversation: Conversation, run: AgentRun, call: ToolCall, tool: Any, arguments: Any, public_args: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if self._cancelled(db, run.id):
            yield {"type": "error", "message": "Task cancelled before tool execution."}; return
        running = self._event(db, conversation.id, run.id, "tool_call", "running", tool.name, call.id, {"arguments": public_args})
        yield self.public_event(running)
        try:
            result = await self.registry.execute(tool, arguments)
            status, payload = "completed", {"result": result}
        except ToolError as exc:
            status, payload = "failed", {"error": str(exc)}
        db.add(Message(conversation_id=conversation.id, role="tool", content=json.dumps({"tool_call_id": call.id, "result": payload.get("result", {"error": payload.get("error")})}, ensure_ascii=False)))
        finished = self._event(db, conversation.id, run.id, "tool_result", status, tool.name, call.id, payload)
        yield self.public_event(finished)

    async def resolve_approval(self, db: Session, conversation: Conversation, approval_id: str, decision: str, provider: ModelProvider) -> AsyncIterator[dict[str, Any]]:
        approval = db.get(ExecutionEvent, approval_id)
        if not approval or approval.conversation_id != conversation.id or approval.event_type != "approval" or approval.status != "waiting_approval":
            yield {"type": "error", "message": "Approval request is invalid, stale, or belongs to another conversation."}; return
        run = db.get(AgentRun, approval.run_id)
        if not run or run.status != "waiting_approval":
            yield {"type": "error", "message": "Approval request is no longer active."}; return
        if decision == "rejected":
            approval.status = "rejected"; run.status = "completed"; db.commit()
            rejected = self._event(db, conversation.id, run.id, "tool_result", "rejected", approval.tool_name, approval.tool_call_id, {"error": "User rejected the approval request."})
            db.add(Message(conversation_id=conversation.id, role="tool", content=json.dumps({"tool_call_id": approval.tool_call_id, "result": {"error": "User rejected the approval request."}}))); db.add(Message(conversation_id=conversation.id, role="assistant", content="I did not run the simulated action because you rejected it.")); db.commit()
            yield self.public_event(rejected); yield {"type": "token", "content": "I did not run the simulated action because you rejected it."}; yield {"type": "done", "run_id": run.id}; return
        approval.status = "approved"; run.status = "running"; db.commit()
        payload = json.loads(approval.payload)
        try:
            tool, arguments, public_args = self.registry.validate(approval.tool_name or "", json.dumps(payload["arguments"]))
        except ToolError as exc:
            yield {"type": "error", "message": str(exc)}; return
        call = ToolCall(approval.tool_call_id or "", approval.tool_name or "", json.dumps(payload["arguments"]))
        async for item in self._execute_validated(db, conversation, run, call, tool, arguments, public_args): yield item
        async for item in self.run(db, conversation, provider, run): yield item

    def cancel(self, db: Session, run_id: str) -> bool:
        run = db.get(AgentRun, run_id)
        if not run or run.status not in {"running", "waiting_approval"}: return False
        run.cancelled = True; run.status = "cancelled"; db.commit(); return True
