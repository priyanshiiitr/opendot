"""Official Telegram Bot API client. Base URL is fixed; the model cannot change it."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096
TOKEN_PATTERN = re.compile(r"\d+:[A-Za-z0-9_-]{20,}")


class TelegramAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: int | None = None):
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)


def redact_secrets(text: str) -> str:
    return TOKEN_PATTERN.sub("[REDACTED_TOKEN]", text)


class TelegramBotAPI:
    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None, timeout: float = 20.0):
        if not token or not TOKEN_PATTERN.fullmatch(token.strip()):
            raise TelegramAPIError("Bot token format is invalid.")
        self._token = token.strip()
        self._timeout = timeout
        self._client = client
        self._poll_lock = asyncio.Lock()

    def _url(self, method: str) -> str:
        # Token is only interpolated into the official host path; never model-controlled.
        return f"{TELEGRAM_API_BASE}/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(self._url(method), json=payload or {})
        except httpx.TimeoutException as exc:
            raise TelegramAPIError("Telegram API request timed out.") from exc
        except httpx.RequestError as exc:
            raise TelegramAPIError("Could not reach the Telegram API.") from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramAPIError("Telegram returned a malformed JSON response.", status_code=response.status_code) from exc

        if response.status_code == 401 or (isinstance(data, dict) and data.get("error_code") == 401):
            raise TelegramAPIError("Telegram rejected the bot token.", status_code=401, error_code=401)
        if response.status_code == 429 or (isinstance(data, dict) and data.get("error_code") == 429):
            raise TelegramAPIError("Telegram rate limit reached. Try again later.", status_code=429, error_code=429)
        if response.status_code == 409 or (isinstance(data, dict) and data.get("error_code") == 409):
            raise TelegramAPIError(
                "Telegram reported a webhook/getUpdates conflict. Remove the webhook or stop other pollers before reading updates.",
                status_code=409,
                error_code=409,
            )
        if not isinstance(data, dict):
            raise TelegramAPIError("Telegram returned an unexpected response shape.", status_code=response.status_code)
        if not data.get("ok"):
            description = redact_secrets(str(data.get("description") or "Telegram API error"))
            raise TelegramAPIError(description, status_code=response.status_code, error_code=data.get("error_code"))
        result = data.get("result")
        if not isinstance(result, (dict, list, int, str, bool)) and result is not None:
            raise TelegramAPIError("Telegram returned an unexpected result payload.")
        return data

    async def get_me(self) -> dict[str, Any]:
        data = await self._call("getMe")
        result = data["result"]
        if not isinstance(result, dict):
            raise TelegramAPIError("getMe returned an unexpected payload.")
        return {
            "id": result.get("id"),
            "is_bot": bool(result.get("is_bot")),
            "username": result.get("username"),
            "first_name": result.get("first_name"),
            "can_join_groups": result.get("can_join_groups"),
            "can_read_all_group_messages": result.get("can_read_all_group_messages"),
            "supports_inline_queries": result.get("supports_inline_queries"),
        }

    async def get_updates(self, *, offset: int | None, limit: int) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 20))
        payload: dict[str, Any] = {"limit": limit, "timeout": 0}
        if offset is not None:
            payload["offset"] = offset
        async with self._poll_lock:
            data = await self._call("getUpdates", payload)
        result = data["result"]
        if not isinstance(result, list):
            raise TelegramAPIError("getUpdates returned an unexpected payload.")
        return result

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        if not text or not text.strip():
            raise TelegramAPIError("Message text must be non-empty.")
        if len(text) > MAX_MESSAGE_LENGTH:
            raise TelegramAPIError(f"Message text exceeds Telegram's {MAX_MESSAGE_LENGTH}-character limit.")
        # No automatic retry: ambiguous network failures must not duplicate sends.
        data = await self._call("sendMessage", {"chat_id": chat_id, "text": text})
        result = data["result"]
        if not isinstance(result, dict):
            raise TelegramAPIError("sendMessage returned an unexpected payload.")
        return {
            "message_id": result.get("message_id"),
            "chat_id": (result.get("chat") or {}).get("id"),
            "date": result.get("date"),
            "text": result.get("text"),
        }
