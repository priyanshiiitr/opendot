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

    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream_turn(self, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]]) -> AsyncIterator[ProviderEvent]:
        latest = messages[-1]
        if latest["role"] == "user":
            original = latest["content"]
            text = original.lower()
            call = self._tool_for_user(text, tools, original)
            if call:
                yield ProviderEvent("tool_calls", tool_calls=(call,))
                return
            response = f"I’m in mock mode, so no paid API call was made. I received: {latest['content']}"
        elif latest["role"] == "tool":
            try:
                result = json.loads(latest["content"])
            except json.JSONDecodeError:
                result = {"error": "unreadable tool result"}
            follow_up = self._tool_for_result(result)
            if follow_up:
                yield ProviderEvent("tool_calls", tool_calls=(follow_up,))
                return
            response = self._final_answer(result)
        else:
            response = "Mock mode completed the request."
        for word in response.split(" "):
            yield ProviderEvent("text", content=word + " ")
            await asyncio.sleep(0.002)

    def _call(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        return ToolCall(f"mock_{uuid.uuid4().hex[:12]}", name, json.dumps(arguments))

    def _tool_names(self, tools: list[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(function, dict) and function.get("name"):
                names.add(str(function["name"]))
        return names

    def _tool_for_user(self, text: str, tools: list[dict[str, Any]], original: str = "") -> ToolCall | None:
        names = self._tool_names(tools)
        if "telegram updates" in text or "telegram messages" in text or "check my telegram" in text:
            if "telegram_get_recent_updates" in names:
                return self._call("telegram_get_recent_updates", {"limit": 10})
        if "telegram bot info" in text and "telegram_get_bot_info" in names:
            return self._call("telegram_get_bot_info", {})
        if "send telegram" in text and "telegram_send_message" in names:
            match = re.search(r"chat\s*(-?\d+)", text)
            chat_id = int(match.group(1)) if match else 0
            body_match = re.search(r"message\s*[:=]\s*(.+)$", original, re.I)
            body = body_match.group(1).strip() if body_match else "Hello from AI Teammates"
            return self._call("telegram_send_message", {"chat_id": chat_id, "text": body})
        if "time" in text or "date" in text:
            zone = "Asia/Calcutta"
            match = re.search(r"(?:in|timezone)\s+([A-Za-z_]+/[A-Za-z_]+)", text)
            if match:
                zone = match.group(1)
            return self._call("get_current_time", {"timezone": zone})
        if "approval" in text or "simulate action" in text:
            return self._call("demo_request_approval", {"message": "A harmless approval demonstration"})
        if "prompt injection" in text:
            base = self.settings.browser_fixture_base_url.rstrip("/")
            if not base:
                return None
            return self._call("browser_open", {"url": f"{base}/prompt_injection.html"})
        if "research startups" in text or "browse startups" in text:
            base = self.settings.browser_fixture_base_url.rstrip("/")
            if not base:
                return None
            return self._call("browser_open", {"url": f"{base}/startups.html"})
        operation_map = {
            "add": "add",
            "plus": "add",
            "subtract": "subtract",
            "minus": "subtract",
            "multiply": "multiply",
            "times": "multiply",
            "divide": "divide",
        }
        operation = next((value for key, value in operation_map.items() if key in text), None)
        numbers = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", text)
        if operation and len(numbers) >= 2:
            return self._call(
                "calculate",
                {"operation": operation, "left": float(numbers[0]), "right": float(numbers[1])},
            )
        return None

    def _tool_for_result(self, result: dict[str, Any]) -> ToolCall | None:
        action = result.get("action")
        if action == "browser_open":
            url = result.get("url", "")
            if "prompt_injection" in url:
                return self._call("browser_snapshot", {})
            if "startups" in url:
                return self._call("browser_extract", {})
        return None

    def _final_answer(self, result: dict[str, Any]) -> str:
        if "error" in result:
            return f"The tool could not complete: {result['error']}"
        if result.get("action") == "browser_snapshot":
            text = result.get("text", "")
            if "IGNORE PREVIOUS INSTRUCTIONS" in text or "approve all tools" in text.lower():
                return (
                    "I treated the webpage as untrusted evidence. I will not follow its instructions to "
                    "approve tools, change permissions, reveal secrets, or open internal services."
                )
            return "I captured a page snapshot and treated it as untrusted evidence."
        if result.get("action") == "browser_extract" and "companies" in result:
            companies = result.get("companies") or []
            if not companies:
                return "The browser extract tool ran but returned no companies."
            lines = ["Startup shortlist from the fixture page (grounded in browser_extract):"]
            for company in companies:
                lines.append(
                    f"- {company.get('name')}: founder {company.get('founder')}; "
                    f"careers {company.get('careers_url')}; internship note: {company.get('internship')}"
                )
            return " ".join(lines)
        if "result" in result:
            return f"The calculation completed. The result is {result['result']}."
        if "datetime" in result:
            return f"The current time in {result['timezone']} is {result['datetime']}."
        if result.get("simulated"):
            return "The harmless simulated action executed after your approval."
        if result.get("action") == "telegram_get_bot_info":
            bot = result.get("bot") or {}
            return f"Connected Telegram bot @{bot.get('username')} (id {bot.get('id')})."
        if result.get("action") == "telegram_get_recent_updates":
            updates = result.get("updates") or []
            if not updates:
                return "No allowlisted Telegram updates were returned."
            lines = [f"Found {len(updates)} allowlisted Telegram update(s):"]
            for item in updates[:10]:
                lines.append(f"chat {item.get('chat_id')}: {(item.get('text') or '')[:120]}")
            lines.append("Message text is untrusted evidence and was not treated as an instruction.")
            return " ".join(lines)
        if result.get("action") == "telegram_send_message" and result.get("ok"):
            tg = result.get("telegram") or {}
            return f"Telegram confirmed send of message_id {tg.get('message_id')} to chat {tg.get('chat_id')}."
        return "The requested local tool completed successfully."


async def _openai_compatible_http_error(response: httpx.Response, *, label: str, auth_hint: str) -> str:
    """Turn an OpenAI-compatible HTTP error into a short, secret-safe UI message."""
    detail = ""
    try:
        raw = (await response.aread()).decode("utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or err.get("error") or "")
            elif isinstance(err, str):
                detail = err
            else:
                detail = str(data.get("message") or "")
    except Exception:
        detail = ""
    detail = re.sub(r"(?i)(bearer\s+|xai-|gsk_|grok_)[A-Za-z0-9._\-]{8,}", "[REDACTED]", detail).strip()
    lowered = detail.lower()
    if response.status_code in (401, 403) or "incorrect api key" in lowered or "invalid api key" in lowered:
        return f"{label} authentication failed. {auth_hint}"
    if response.status_code == 429:
        return f"{label} rate limit reached. Try again later."
    if detail:
        return f"{label} request failed ({response.status_code}): {detail}"
    return f"{label} request failed ({response.status_code})."


class OpenAICompatibleProvider(ModelProvider):
    """Shared SSE chat-completions client for OpenAI-compatible hosts (xAI, Groq, …)."""

    def __init__(
        self,
        *,
        label: str,
        api_key: str,
        base_url: str,
        model: str,
        auth_hint: str,
        missing_key_message: str,
    ):
        if not api_key:
            raise ProviderError(missing_key_message)
        self.label = label
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.auth_hint = auth_hint

    async def stream_turn(self, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]]) -> AsyncIterator[ProviderEvent]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        fragments: dict[int, dict[str, str]] = {}
        saw_done = False
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as response:
                    if response.status_code >= 400:
                        raise ProviderError(
                            await _openai_compatible_http_error(response, label=self.label, auth_hint=self.auth_hint)
                        )
                    async for line in response.aiter_lines():
                        if line == "data: [DONE]":
                            saw_done = True
                            continue
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError as exc:
                            raise ProviderError(f"{self.label} returned malformed streaming JSON.") from exc
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        if content := delta.get("content"):
                            yield ProviderEvent("text", content=content)
                        for item in delta.get("tool_calls", []) or []:
                            index = item.get("index")
                            if not isinstance(index, int):
                                raise ProviderError(f"{self.label} returned an incomplete tool-call index.")
                            record = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            record["id"] += item.get("id", "")
                            function = item.get("function", {})
                            record["name"] += function.get("name", "")
                            record["arguments"] += function.get("arguments", "")
            if fragments:
                calls = []
                for item in fragments.values():
                    if not item["id"] or not item["name"] or not item["arguments"]:
                        raise ProviderError(f"{self.label} stream ended with an incomplete tool call.")
                    try:
                        json.loads(item["arguments"])
                    except json.JSONDecodeError as exc:
                        raise ProviderError(f"{self.label} returned invalid tool-call arguments.") from exc
                    calls.append(ToolCall(item["id"], item["name"], item["arguments"]))
                yield ProviderEvent("tool_calls", tool_calls=tuple(calls))
            elif not saw_done:
                raise ProviderError(f"{self.label} stream ended unexpectedly.")
        except httpx.RequestError as exc:
            raise ProviderError(f"Could not reach {self.label}.") from exc


class XAIProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings):
        super().__init__(
            label="xAI",
            api_key=settings.xai_api_key or "",
            base_url=settings.xai_base_url,
            model=settings.xai_model,
            auth_hint="Use a key from console.x.ai (starts with xai-) in AI_TEAMMATES_XAI_API_KEY, then restart the backend.",
            missing_key_message="xAI is selected but AI_TEAMMATES_XAI_API_KEY is missing.",
        )


class GroqProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings):
        super().__init__(
            label="Groq",
            api_key=settings.groq_api_key or "",
            base_url=settings.groq_base_url,
            model=settings.groq_model,
            auth_hint="Use a key from console.groq.com (usually starts with gsk_) in AI_TEAMMATES_GROQ_API_KEY, then restart the backend.",
            missing_key_message="Groq is selected but AI_TEAMMATES_GROQ_API_KEY is missing.",
        )


def build_provider(settings: Settings) -> ModelProvider:
    provider = settings.model_provider.lower().strip()
    if provider == "xai":
        return XAIProvider(settings)
    if provider == "groq":
        return GroqProvider(settings)
    return MockProvider(settings)
