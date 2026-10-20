import json
from fastapi.testclient import TestClient
from app.main import app
from app.tools import ToolError, ToolRegistry

def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

def new_conversation(client):
    teammate = client.get("/api/teammates").json()[0]
    return client.post(f"/api/teammates/{teammate['id']}/conversations").json()

def test_registry_validates_and_calculates():
    registry = ToolRegistry("Asia/Calcutta", 1, 1000, 1000)
    tool, arguments, _ = registry.validate("calculate", '{"operation":"multiply","left":6,"right":7}')
    import asyncio
    assert asyncio.run(registry.execute(tool, arguments))["result"] == 42
    for bad in ('not-json', '{"operation":"power","left":2,"right":3}'):
        try: registry.validate("calculate", bad)
        except ToolError: pass
        else: assert False
    try: registry.validate("not_registered", "{}")
    except ToolError as exc: assert "Unknown" in str(exc)
    else: assert False
    tool, args, _ = registry.validate("calculate", '{"operation":"divide","left":1,"right":0}')
    try: asyncio.run(registry.execute(tool, args))
    except ToolError as exc: assert "zero" in str(exc).lower()
    else: assert False

def test_mock_tool_execution_and_persistence():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content":"Please add 12 plus 30"})
        stream = events(response)
        assert any(e.get("event_type") == "tool_result" and e.get("status") == "completed" for e in stream)
        assert "42" in "".join(e.get("content", "") for e in stream)
        saved = client.get(f"/api/conversations/{conversation['id']}").json()
        assert any(e["tool_name"] == "calculate" for e in saved["execution_events"])
        assert "result is 42.0" in saved["messages"][-1]["content"]

def test_approval_requires_explicit_decision_and_rejection_does_not_execute():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        pending = events(client.post(f"/api/conversations/{conversation['id']}/messages", json={"content":"request approval"}))
        approval = next(e for e in pending if e["type"] == "approval_required")
        saved = client.get(f"/api/conversations/{conversation['id']}").json()
        assert not any(e["tool_name"] == "demo_request_approval" and e["event_type"] == "tool_result" for e in saved["execution_events"])
        rejected = events(client.post(f"/api/conversations/{conversation['id']}/approvals/{approval['approval_id']}", json={"decision":"rejected"}))
        assert any(e.get("status") == "rejected" for e in rejected)
        assert "did not run" in "".join(e.get("content", "") for e in rejected)

def test_approval_executes_only_after_approval_and_wrong_conversation_fails():
    with TestClient(app) as client:
        first, second = new_conversation(client), new_conversation(client)
        pending = events(client.post(f"/api/conversations/{first['id']}/messages", json={"content":"request approval"}))
        approval_id = next(e for e in pending if e["type"] == "approval_required")["approval_id"]
        wrong = events(client.post(f"/api/conversations/{second['id']}/approvals/{approval_id}", json={"decision":"approved"}))
        assert wrong[0]["type"] == "error"
        approved = events(client.post(f"/api/conversations/{first['id']}/approvals/{approval_id}", json={"decision":"approved"}))
        assert any(e.get("event_type") == "tool_result" and e.get("status") == "completed" for e in approved)
        assert "after your approval" in "".join(e.get("content", "") for e in approved)

def test_cancellation_blocks_pending_approval_execution():
    with TestClient(app) as client:
        conversation = new_conversation(client)
        pending = events(client.post(f"/api/conversations/{conversation['id']}/messages", json={"content":"request approval"}))
        approval = next(e for e in pending if e["type"] == "approval_required")
        run_id = next(e for e in pending if e["type"] == "run_started")["run_id"]
        assert client.post(f"/api/runs/{run_id}/cancel").json() == {"cancelled": True}
        blocked = events(client.post(f"/api/conversations/{conversation['id']}/approvals/{approval['approval_id']}", json={"decision":"approved"}))
        assert blocked[0]["type"] == "error"
