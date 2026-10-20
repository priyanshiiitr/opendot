import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .config import Settings


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderEvent:
    type: Literal["text", "tool_calls"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class ModelProvider:
    async def stream_turn(self, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]]) -> AsyncIterator[ProviderEvent]:
        raise NotImplementedError


class MockProvider(ModelProvider):
    """Deterministic local scenarios proving that real backend tools are used."""
    async def stream_turn(self, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]]) -> AsyncIterator[ProviderEvent]:
        latest = messages[-1]
        if latest["role"] == "user":
            text = latest["content"].lower()
            call = self._tool_for(text)
            if call:
                yield ProviderEvent("tool_calls", tool_calls=(call,))
                return
            response = f"I’m in mock mode, so no paid API call was made. I received: {latest['content']}"
        elif latest["role"] == "tool":
            try:
                result = json.loads(latest["content"])
            except json.JSONDecodeError:
                result = {"error": "unreadable tool result"}
            response = self._final_answer(result)
        else:
            response = "Mock mode completed the request."
        for word in response.split(" "):
            yield ProviderEvent("text", content=word + " ")
            await asyncio.sleep(0.002)

    def _tool_for(self, text: str) -> ToolCall | None:
        call_id = f"mock_{uuid.uuid4().hex[:12]}"
        if "time" in text or "date" in text:
            zone = "Asia/Calcutta"
            match = re.search(r"(?:in|timezone)\s+([A-Za-z_]+/[A-Za-z_]+)", text)
            if match:
                zone = match.group(1)
            return ToolCall(call_id, "get_current_time", json.dumps({"timezone": zone}))
        if "approval" in text or "simulate action" in text:
            return ToolCall(call_id, "demo_request_approval", json.dumps({"message": "A harmless approval demonstration"}))
        operation_map = {"add": "add", "plus": "add", "subtract": "subtract", "minus": "subtract", "multiply": "multiply", "times": "multiply", "divide": "divide"}
        operation = next((value for key, value in operation_map.items() if key in text), None)
        numbers = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", text)
        if operation and len(numbers) >= 2:
            return ToolCall(call_id, "calculate", json.dumps({"operation": operation, "left": float(numbers[0]), "right": float(numbers[1])}))
        return None

    def _final_answer(self, result: dict[str, Any]) -> str:
        if "error" in result:
            return f"The tool could not complete: {result['error']}"
        if "result" in result:
            return f"The calculation completed. The result is {result['result']}."
        if "datetime" in result:
            return f"The current time in {result['timezone']} is {result['datetime']}."
        if result.get("simulated"):
            return "The harmless simulated action executed after your approval."
        return "The requested local tool completed successfully."


class XAIProvider(ModelProvider):
    def __init__(self, settings: Settings):
        if not settings.xai_api_key:
            raise ProviderError("xAI is selected but AI_TEAMMATES_XAI_API_KEY is missing.")
        self.settings = settings

    async def stream_turn(self, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]]) -> AsyncIterator[ProviderEvent]:
        payload = {"model": self.settings.xai_model, "messages": [{"role": "system", "content": system_prompt}, *messages], "tools": tools, "tool_choice": "auto", "parallel_tool_calls": False, "stream": True}
        headers = {"Authorization": f"Bearer {self.settings.xai_api_key}", "Content-Type": "application/json"}
        fragments: dict[int, dict[str, str]] = {}
        saw_done = False
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", f"{self.settings.xai_base_url}/chat/completions", json=payload, headers=headers) as response:
                    if response.status_code in (401, 403): raise ProviderError("xAI authentication failed. Check the API key and model access.")
                    if response.status_code == 429: raise ProviderError("xAI rate limit reached. Try again later.")
                    if response.status_code >= 400: raise ProviderError(f"xAI request failed ({response.status_code}).")
                    async for line in response.aiter_lines():
                        if line == "data: [DONE]":
                            saw_done = True; continue
                        if not line.startswith("data: "): continue
                        try: data = json.loads(line[6:])
                        except json.JSONDecodeError as exc: raise ProviderError("xAI returned malformed streaming JSON.") from exc
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        if content := delta.get("content"):
                            yield ProviderEvent("text", content=content)
                        for item in delta.get("tool_calls", []) or []:
                            index = item.get("index")
                            if not isinstance(index, int): raise ProviderError("xAI returned an incomplete tool-call index.")
                            record = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            record["id"] += item.get("id", "")
                            function = item.get("function", {})
                            record["name"] += function.get("name", "")
                            record["arguments"] += function.get("arguments", "")
            if fragments:
                calls = []
                for item in fragments.values():
                    if not item["id"] or not item["name"] or not item["arguments"]:
                        raise ProviderError("xAI stream ended with an incomplete tool call.")
                    try: json.loads(item["arguments"])
                    except json.JSONDecodeError as exc: raise ProviderError("xAI returned invalid tool-call arguments.") from exc
                    calls.append(ToolCall(item["id"], item["name"], item["arguments"]))
                yield ProviderEvent("tool_calls", tool_calls=tuple(calls))
            elif not saw_done:
                raise ProviderError("xAI stream ended unexpectedly.")
        except httpx.RequestError as exc:
            raise ProviderError("Could not reach xAI.") from exc


def build_provider(settings: Settings) -> ModelProvider:
    return XAIProvider(settings) if settings.model_provider.lower() == "xai" else MockProvider()
