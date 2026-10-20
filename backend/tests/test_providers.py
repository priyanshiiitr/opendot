import asyncio
from app.config import Settings
from app.providers import ProviderError, XAIProvider

class FakeResponse:
    status_code = 200
    def __init__(self, lines): self.lines = lines
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def aiter_lines(self):
        for line in self.lines: yield line

class FakeClient:
    def __init__(self, *args, **kwargs): self.request = None
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def stream(self, method, url, json, headers):
        FakeClient.request = {"method": method, "url": url, "json": json, "headers": headers}
        return FakeResponse([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"cal"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"culate","arguments":"{\\"operation\\":\\"add\\","}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"left\\":2,\\"right\\":3}"}}]}}]}',
            'data: [DONE]',
        ])

def test_xai_accumulates_fragmented_tool_call(monkeypatch):
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = XAIProvider(Settings(xai_api_key="test-key", model_provider="xai"))
    events = asyncio.run(collect(provider))
    call = events[-1].tool_calls[0]
    assert call.id == "call_1" and call.name == "calculate"
    assert call.arguments == '{"operation":"add","left":2,"right":3}'
    assert FakeClient.request["json"]["tools"]
    assert FakeClient.request["headers"]["Authorization"] == "Bearer test-key"

async def collect(provider):
    return [event async for event in provider.stream_turn([{"role":"user", "content":"add"}], "system", [{"type":"function"}])]
