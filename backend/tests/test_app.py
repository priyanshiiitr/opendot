import json
import os
os.environ["AI_TEAMMATES_DATABASE_URL"] = "sqlite:///./test_ai_teammates.db"
os.environ["AI_TEAMMATES_MODEL_PROVIDER"] = "mock"
from fastapi.testclient import TestClient
from app.main import app

def test_health_and_default_teammate():
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/teammates").json()[0]["name"] == "Atlas"

def test_conversation_persists_mock_response():
    with TestClient(app) as client:
        teammate_id = client.get("/api/teammates").json()[0]["id"]
        conversation = client.post(f"/api/teammates/{teammate_id}/conversations").json()
        response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "Hello"})
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert any(event["type"] == "token" for event in events)
        assert events[-1]["type"] == "done"
        saved = client.get(f"/api/conversations/{conversation['id']}").json()
        assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
        assert "mock mode" in saved["messages"][-1]["content"]
