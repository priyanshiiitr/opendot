import asyncio
import json

import pytest

from app.config import Settings
from app.providers import GroqProvider, ProviderError, XAIProvider, build_provider


class FakeResponse:
    status_code = 200
    body = b""

    def __init__(self, lines, *, status_code=200, body=b""):
        self.lines = lines
        self.status_code = status_code
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aread(self):
        return self.body

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def stream(self, method, url, json, headers):
        FakeClient.last_request = {"method": method, "url": url, "json": json, "headers": headers}
        return FakeResponse(
            FakeClient.lines,
            status_code=getattr(FakeClient, "status_code", 200),
            body=getattr(FakeClient, "body", b""),
        )


async def collect(provider):
    return [
        event
        async for event in provider.stream_turn(
            [{"role": "user", "content": "add"}],
            "system",
            [{"type": "function", "function": {"name": "calculate"}}],
        )
    ]


def test_xai_surfaces_incorrect_api_key(monkeypatch):
    FakeClient.lines = []
    FakeClient.status_code = 400
    FakeClient.body = b'{"code":"invalid-argument","error":"Incorrect API key provided."}'
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = XAIProvider(Settings(xai_api_key="grok_not_a_real_key", model_provider="xai"))
    with pytest.raises(ProviderError, match="authentication failed"):
        asyncio.run(collect(provider))


def test_groq_provider_uses_groq_base_url(monkeypatch):
    FakeClient.lines = ['data: {"choices":[{"delta":{"content":"hi"}}]}', "data: [DONE]"]
    FakeClient.status_code = 200
    FakeClient.body = b""
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = GroqProvider(
        Settings(groq_api_key="gsk_test", model_provider="groq", groq_model="openai/gpt-oss-20b")
    )
    events = asyncio.run(collect(provider))
    assert events[-1].content == "hi"
    assert FakeClient.last_request["url"].startswith("https://api.groq.com/openai/v1/chat/completions")
    assert FakeClient.last_request["json"]["model"] == "openai/gpt-oss-20b"
    assert build_provider(Settings(model_provider="groq", groq_api_key="gsk_test")).__class__.__name__ == "GroqProvider"


def test_xai_accumulates_fragmented_tool_call(monkeypatch):
    FakeClient.lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"cal"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"culate","arguments":"{\\"operation\\":\\"add\\","}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"left\\":2,\\"right\\":3}"}}]}}]}',
        "data: [DONE]",
    ]
    FakeClient.status_code = 200
    FakeClient.body = b""
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = XAIProvider(Settings(xai_api_key="test-key", model_provider="xai"))
    events = asyncio.run(collect(provider))
    call = events[-1].tool_calls[0]
    assert call.id == "call_1" and call.name == "calculate"
    assert call.arguments == '{"operation":"add","left":2,"right":3}'
    assert FakeClient.last_request["json"]["tools"]
    assert FakeClient.last_request["json"]["parallel_tool_calls"] is False
    assert FakeClient.last_request["headers"]["Authorization"] == "Bearer test-key"


def test_xai_rejects_incomplete_tool_call_fragments(monkeypatch):
    FakeClient.lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"calculate","arguments":"{\\"operation\\":"}}]}}]}',
        "data: [DONE]",
    ]
    FakeClient.status_code = 200
    FakeClient.body = b""
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = XAIProvider(Settings(xai_api_key="test-key", model_provider="xai"))
    with pytest.raises(ProviderError, match="invalid tool-call arguments"):
        asyncio.run(collect(provider))


def test_xai_rejects_missing_tool_call_identity(monkeypatch):
    FakeClient.lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}',
        "data: [DONE]",
    ]
    FakeClient.status_code = 200
    FakeClient.body = b""
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = XAIProvider(Settings(xai_api_key="test-key", model_provider="xai"))
    with pytest.raises(ProviderError, match="incomplete tool call"):
        asyncio.run(collect(provider))


def test_xai_preserves_tool_result_messages_in_request(monkeypatch):
    FakeClient.lines = ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]
    FakeClient.status_code = 200
    FakeClient.body = b""
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = XAIProvider(Settings(xai_api_key="test-key", model_provider="xai"))
    history = [
        {"role": "user", "content": "add 2 and 3"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "calculate", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"result": 5})},
    ]

    async def run():
        return [event async for event in provider.stream_turn(history, "system", [{"type": "function"}])]

    events = asyncio.run(run())
    assert events[-1].content == "ok"
    assert FakeClient.last_request["json"]["messages"][-1]["role"] == "tool"
    assert FakeClient.last_request["json"]["messages"][-1]["tool_call_id"] == "call_1"
