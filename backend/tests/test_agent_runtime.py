import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from fastapi.testclient import TestClient

from app.main import app, runtime
from app.providers import ProviderEvent, ToolCall
from app.tools import CalculateArguments, ToolError, ToolRegistry, ToolSpec


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def new_conversation(client):
    teammate = client.get("/api/teammates").json()[0]
    return client.post(f"/api/teammates/{teammate['id']}/conversations").json()


def test_registry_validates_and_calculates():
    registry = ToolRegistry("Asia/Calcutta", 1, 1000, 1000)
    tool, arguments, _ = registry.validate("calculate", '{"operation":"multiply","left":6,"right":7}')
    assert asyncio.run(registry.execute(tool, arguments))["result"] == 42
    for bad in ("not-json", '{"operation":"power","left":2,"right":3}', "[]"):
        try:
            registry.validate("calculate", bad)
        except ToolError:
            pass
        else:
            assert False, bad
    try:
        registry.validate("not_registered", "{}")
    except ToolError as exc:
        assert "Unknown" in str(exc)
    else:
        assert False
    tool, args, _ = registry.validate("calculate", '{"operation":"divide","left":1,"right":0}')
    try:
        asyncio.run(registry.execute(tool, args))
    except ToolError as exc:
        assert "zero" in str(exc).lower()
    else:
        assert False


def test_invalid_arguments_never_reach_executor():
    registry = ToolRegistry("Asia/Calcutta", 1, 1000, 1000)
    called = {"count": 0}

    async def spy(_arguments):
        called["count"] += 1
        return {"result": 1}

    registry._tools["calculate"] = ToolSpec(
        "calculate",
        "spy",
        CalculateArguments,
        "read_only",
        1,
        spy,
    )
    try:
        registry.validate("calculate", '{"operation":"add","left":"nope","right":1}')
    except ToolError:
        pass
    else:
        assert False
    assert called["count"] == 0


def test_get_current_time_returns_timezone_aware_value():
    registry = ToolRegistry("Asia/Calcutta", 1, 1000, 1000)
    tool, arguments, public_args = registry.validate("get_current_time", "{}")
    assert public_args["timezone"] == "Asia/Calcutta"
    result = asyncio.run(registry.execute(tool, arguments))
    assert result["timezone"] == "Asia/Calcutta"
    assert "T" in result["datetime"]
    assert isinstance(result["unix_timestamp"], int)


def test_mock_tool_execution_and_persistence():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        response = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "Please add 12 plus 30"},
        )
        stream = events(response)
        assert any(e.get("status") == "validated" for e in stream)
        assert any(e.get("event_type") == "tool_result" and e.get("status") == "completed" for e in stream)
        assert "42" in "".join(e.get("content", "") for e in stream)
        assert stream[-1]["type"] == "done"
        saved = client.get(f"/api/conversations/{conversation['id']}").json()
        assert any(e["tool_name"] == "calculate" for e in saved["execution_events"])
        assert "result is 42.0" in saved["messages"][-1]["content"]


def test_time_tool_executes_in_mock_mode():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        stream = events(
            client.post(
                f"/api/conversations/{conversation['id']}/messages",
                json={"content": "what time is it?"},
            )
        )
        assert any(e.get("tool_name") == "get_current_time" and e.get("status") == "completed" for e in stream)
        assert "current time" in "".join(e.get("content", "") for e in stream).lower()


def test_approval_requires_explicit_decision_and_rejection_does_not_execute():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        pending = events(client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "request approval"}))
        approval = next(e for e in pending if e["type"] == "approval_required")
        saved = client.get(f"/api/conversations/{conversation['id']}").json()
        assert not any(
            e["tool_name"] == "demo_request_approval" and e["event_type"] == "tool_result" for e in saved["execution_events"]
        )
        rejected = events(
            client.post(
                f"/api/conversations/{conversation['id']}/approvals/{approval['approval_id']}",
                json={"decision": "rejected"},
            )
        )
        assert any(e.get("status") == "rejected" for e in rejected)
        assert "did not run" in "".join(e.get("content", "") for e in rejected)


def test_approval_executes_only_after_approval_and_wrong_conversation_fails():
    with TestClient(app) as client:
        first, second = new_conversation(client), new_conversation(client)
        pending = events(client.post(f"/api/conversations/{first['id']}/messages", json={"content": "request approval"}))
        approval_id = next(e for e in pending if e["type"] == "approval_required")["approval_id"]
        wrong = events(client.post(f"/api/conversations/{second['id']}/approvals/{approval_id}", json={"decision": "approved"}))
        assert wrong[0]["type"] == "error"
        approved = events(client.post(f"/api/conversations/{first['id']}/approvals/{approval_id}", json={"decision": "approved"}))
        assert any(e.get("event_type") == "tool_result" and e.get("status") == "completed" for e in approved)
        assert "after your approval" in "".join(e.get("content", "") for e in approved)
        duplicate = events(client.post(f"/api/conversations/{first['id']}/approvals/{approval_id}", json={"decision": "approved"}))
        assert duplicate[0]["type"] == "error"


def test_cancellation_blocks_pending_approval_execution():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        pending = events(client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "request approval"}))
        approval = next(e for e in pending if e["type"] == "approval_required")
        run_id = next(e for e in pending if e["type"] == "run_started")["run_id"]
        assert client.post(f"/api/runs/{run_id}/cancel").json() == {"cancelled": True}
        blocked = events(
            client.post(
                f"/api/conversations/{conversation['id']}/approvals/{approval['approval_id']}",
                json={"decision": "approved"},
            )
        )
        assert blocked[0]["type"] == "error"


def test_iteration_limit_stops_runaway_tool_loop(monkeypatch):
    original_max = runtime.settings.agent_max_iterations
    runtime.settings.agent_max_iterations = 2

    class RunawayProvider:
        async def stream_turn(
            self, _messages: list[dict[str, Any]], _system: str, _tools: list[dict[str, Any]]
        ) -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(
                "tool_calls",
                tool_calls=(ToolCall("runaway_1", "calculate", '{"operation":"add","left":1,"right":1}'),),
            )

    monkeypatch.setattr("app.main.build_provider", lambda _settings: RunawayProvider())
    try:
        with TestClient(app) as client:
            conversation = new_conversation(client)
            stream = events(
                client.post(
                    f"/api/conversations/{conversation['id']}/messages",
                    json={"content": "Please add 1 plus 1"},
                )
            )
            assert any(e.get("type") == "error" and "iteration" in e.get("message", "").lower() for e in stream)
    finally:
        runtime.settings.agent_max_iterations = original_max


def test_tool_call_limit_stops_excess_calls(monkeypatch):
    original_max = runtime.settings.agent_max_tool_calls
    runtime.settings.agent_max_tool_calls = 1

    class MultiCallProvider:
        async def stream_turn(
            self, _messages: list[dict[str, Any]], _system: str, _tools: list[dict[str, Any]]
        ) -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(
                "tool_calls",
                tool_calls=(
                    ToolCall("c1", "calculate", '{"operation":"add","left":1,"right":1}'),
                    ToolCall("c2", "calculate", '{"operation":"add","left":2,"right":2}'),
                ),
            )

    monkeypatch.setattr("app.main.build_provider", lambda _settings: MultiCallProvider())
    try:
        with TestClient(app) as client:
            conversation = new_conversation(client)
            stream = events(
                client.post(
                    f"/api/conversations/{conversation['id']}/messages",
                    json={"content": "Please add 1 plus 1"},
                )
            )
            assert any(e.get("type") == "error" and "tool-call limit" in e.get("message", "").lower() for e in stream)
    finally:
        runtime.settings.agent_max_tool_calls = original_max
