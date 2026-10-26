"""GitHub + Gmail OAuth connectors — code exchange + scoped tools (keyring tokens)."""

from __future__ import annotations

import json
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..tools import ToolError, ToolRegistry, ToolSpec
from .base import Connector, ConnectorCapability, ConnectorStatus
from .credentials import CredentialStore

if TYPE_CHECKING:
    pass

GITHUB_TOOL_NAMES = ("github_list_repos", "github_get_me")
GMAIL_TOOL_NAMES = ("gmail_list_messages", "gmail_get_profile")

_PENDING_STATES: dict[str, dict[str, Any]] = {}


def _http_json(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None) -> dict:
    req = Request(url, data=data, headers=headers or {}, method=method)
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8") or "{}"
        return json.loads(raw) if raw.strip() else {}


class OAuthConnector(Connector):
    """Shared OAuth pattern: authorize URL → callback → keyring token → tools."""

    provider: str  # github | gmail
    authorize_url: str
    token_url: str
    scopes: str
    token_key: str
    meta_key: str

    def __init__(self, store: CredentialStore, settings: Settings | None = None):
        self.store = store
        self.settings = settings or get_settings()

    @property
    def _oauth_enabled(self) -> bool:
        return bool(self.settings.oauth_connectors_enabled)

    @property
    def _client_id(self) -> str:
        raise NotImplementedError

    @property
    def _client_secret(self) -> str:
        raise NotImplementedError

    @property
    def _redirect_uri(self) -> str:
        base = (getattr(self.settings, "oauth_redirect_base", None) or "http://127.0.0.1:8000").rstrip("/")
        return f"{base}/api/connectors/{self.id}/callback"

    def _creds_present(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _access_token(self) -> str | None:
        return self.store.get_secret(self.token_key)

    def status(self) -> ConnectorStatus:
        token = self._access_token()
        ready = self._oauth_enabled and self._creds_present()
        connected = bool(token)
        return ConnectorStatus(
            id=self.id,
            display_name=self.display_name,
            connected=connected,
            healthy=connected,
            credentials_writable=self.store.writable and ready,
            capabilities=self._capabilities(),
            message=self._status_message(ready, connected),
            token_configured=bool(token) or self._creds_present(),
        )

    def _capabilities(self) -> list[ConnectorCapability]:
        return []

    def _status_message(self, ready: bool, connected: bool) -> str:
        if not self._oauth_enabled:
            return (
                f"Refuses work until AI_TEAMMATES_OAUTH_CONNECTORS_ENABLED=true and "
                f"client id/secret are set for {self.display_name}."
            )
        if not self._creds_present():
            return f"{self.display_name}: OAuth client id/secret missing in env."
        if connected:
            return f"{self.display_name} connected (token in keyring). Tools are live."
        return (
            f"{self.display_name} OAuth app configured. Open authorize URL from Connections to connect."
        )

    async def verify(self) -> ConnectorStatus:
        token = self._access_token()
        if not token:
            st = self.status()
            st.healthy = False
            st.message = f"{self.display_name}: not connected."
            return st
        try:
            await self._verify_token(token)
            st = self.status()
            st.healthy = True
            st.message = f"{self.display_name}: token verified."
            return st
        except Exception as exc:  # noqa: BLE001
            st = self.status()
            st.healthy = False
            st.message = f"{self.display_name}: verify failed — {exc}"
            return st

    async def _verify_token(self, token: str) -> None:
        raise NotImplementedError

    async def configure(self, token: str, chat_allowlist: list[int]) -> ConnectorStatus:
        _ = token, chat_allowlist
        st = self.status()
        st.message = (
            f"Use GET /api/connectors/{self.id}/authorize — do not paste OAuth tokens into configure."
        )
        return st

    async def disconnect(self) -> ConnectorStatus:
        try:
            self.store.delete_secret(self.token_key)
            self.store.delete_secret(self.meta_key)
        except Exception:
            pass
        st = self.status()
        st.connected = False
        st.message = f"{self.display_name} disconnected."
        return st

    def begin_authorize(self) -> dict[str, str]:
        if not self._oauth_enabled or not self._creds_present():
            raise RuntimeError(
                f"{self.display_name} OAuth not ready. Enable OAUTH_CONNECTORS and set client id/secret."
            )
        state = secrets.token_urlsafe(24)
        _PENDING_STATES[state] = {"provider": self.id, "created": time.time()}
        # Drop stale states (>30m)
        cutoff = time.time() - 1800
        for k, v in list(_PENDING_STATES.items()):
            if v.get("created", 0) < cutoff:
                _PENDING_STATES.pop(k, None)
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "state": state,
            "scope": self.scopes,
            "response_type": "code",
        }
        if self.id == "gmail":
            params["access_type"] = "offline"
            params["prompt"] = "consent"
        return {"authorize_url": f"{self.authorize_url}?{urlencode(params)}", "state": state}

    async def finish_authorize(self, code: str, state: str) -> ConnectorStatus:
        pending = _PENDING_STATES.pop(state, None)
        if not pending or pending.get("provider") != self.id:
            raise RuntimeError("Invalid or expired OAuth state.")
        if not self._creds_present():
            raise RuntimeError("OAuth client credentials missing.")
        token_payload = await self._exchange_code(code)
        access = token_payload.get("access_token")
        if not access:
            raise RuntimeError(f"Token exchange failed: {token_payload}")
        self.store.set_secret(self.token_key, access)
        meta = {k: token_payload.get(k) for k in ("refresh_token", "expires_in", "token_type", "scope")}
        self.store.set_secret(self.meta_key, json.dumps({k: v for k, v in meta.items() if v is not None}))
        return self.status()

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        raise NotImplementedError

    def register_tools(self, registry: ToolRegistry) -> None:
        pass

    def unregister_tools(self, registry: ToolRegistry) -> None:
        pass


class EmptyArgs(BaseModel):
    pass


class GithubListReposArgs(BaseModel):
    per_page: int = Field(default=10, ge=1, le=30)


class GmailListMessagesArgs(BaseModel):
    max_results: int = Field(default=10, ge=1, le=20)
    query: str = Field(default="", max_length=200)


class GitHubOAuthConnector(OAuthConnector):
    id = "github"
    display_name = "GitHub"
    provider = "github"
    authorize_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"
    scopes = "read:user repo"
    token_key = "github_oauth_access_token"
    meta_key = "github_oauth_meta"

    @property
    def _client_id(self) -> str:
        return (self.settings.github_oauth_client_id or "").strip()

    @property
    def _client_secret(self) -> str:
        return (self.settings.github_oauth_client_secret or "").strip()

    def _capabilities(self) -> list[ConnectorCapability]:
        return [
            ConnectorCapability("github_get_me", "Authenticated GitHub user profile", "read_only"),
            ConnectorCapability("github_list_repos", "List repositories for the user", "read_only"),
        ]

    async def _verify_token(self, token: str) -> None:
        _http_json(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "ai-teammates"},
        )

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        body = urlencode(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
            }
        ).encode()
        return _http_json(
            self.token_url,
            method="POST",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ai-teammates",
            },
        )

    def register_tools(self, registry: ToolRegistry) -> None:
        if not self._access_token():
            return

        async def get_me(_args: EmptyArgs) -> dict[str, Any]:
            token = self._access_token()
            if not token:
                raise ToolError("GitHub not connected.")
            user = _http_json(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "ai-teammates",
                },
            )
            return {"action": "github_get_me", "login": user.get("login"), "id": user.get("id"), "name": user.get("name")}

        async def list_repos(args: GithubListReposArgs) -> dict[str, Any]:
            token = self._access_token()
            if not token:
                raise ToolError("GitHub not connected.")
            repos = _http_json(
                f"https://api.github.com/user/repos?per_page={args.per_page}&sort=updated",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "ai-teammates",
                },
            )
            if not isinstance(repos, list):
                raise ToolError("Unexpected GitHub repos response.")
            return {
                "action": "github_list_repos",
                "repos": [
                    {"full_name": r.get("full_name"), "private": r.get("private"), "html_url": r.get("html_url")}
                    for r in repos
                ],
            }

        registry.register(
            ToolSpec(
                "github_get_me",
                "Get the authenticated GitHub user profile.",
                EmptyArgs,
                "read_only",
                20.0,
                get_me,
            )
        )
        registry.register(
            ToolSpec(
                "github_list_repos",
                "List repositories for the authenticated GitHub user.",
                GithubListReposArgs,
                "read_only",
                20.0,
                list_repos,
            )
        )

    def unregister_tools(self, registry: ToolRegistry) -> None:
        for name in GITHUB_TOOL_NAMES:
            registry.unregister(name)


class GmailOAuthConnector(OAuthConnector):
    id = "gmail"
    display_name = "Gmail"
    provider = "gmail"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/userinfo.email"
    token_key = "gmail_oauth_access_token"
    meta_key = "gmail_oauth_meta"

    @property
    def _client_id(self) -> str:
        return (self.settings.gmail_oauth_client_id or "").strip()

    @property
    def _client_secret(self) -> str:
        return (self.settings.gmail_oauth_client_secret or "").strip()

    def _capabilities(self) -> list[ConnectorCapability]:
        return [
            ConnectorCapability("gmail_get_profile", "Gmail profile / email address", "read_only"),
            ConnectorCapability("gmail_list_messages", "List recent Gmail message metadata", "read_only"),
        ]

    async def _verify_token(self, token: str) -> None:
        _http_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        body = urlencode(
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode()
        return _http_json(
            self.token_url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def register_tools(self, registry: ToolRegistry) -> None:
        if not self._access_token():
            return

        async def get_profile(_args: EmptyArgs) -> dict[str, Any]:
            token = self._access_token()
            if not token:
                raise ToolError("Gmail not connected.")
            profile = _http_json(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {token}"},
            )
            return {
                "action": "gmail_get_profile",
                "email_address": profile.get("emailAddress"),
                "messages_total": profile.get("messagesTotal"),
            }

        async def list_messages(args: GmailListMessagesArgs) -> dict[str, Any]:
            token = self._access_token()
            if not token:
                raise ToolError("Gmail not connected.")
            q = urlencode({"maxResults": args.max_results, "q": args.query.strip()})
            listing = _http_json(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{q}",
                headers={"Authorization": f"Bearer {token}"},
            )
            msgs = listing.get("messages") or []
            return {
                "action": "gmail_list_messages",
                "result_size_estimate": listing.get("resultSizeEstimate"),
                "messages": [{"id": m.get("id"), "thread_id": m.get("threadId")} for m in msgs],
                "note": "Metadata only — message bodies are not fetched by default.",
            }

        registry.register(
            ToolSpec(
                "gmail_get_profile",
                "Get the connected Gmail profile (email address).",
                EmptyArgs,
                "read_only",
                20.0,
                get_profile,
            )
        )
        registry.register(
            ToolSpec(
                "gmail_list_messages",
                "List recent Gmail message ids (metadata only).",
                GmailListMessagesArgs,
                "read_only",
                20.0,
                list_messages,
            )
        )

    def unregister_tools(self, registry: ToolRegistry) -> None:
        for name in GMAIL_TOOL_NAMES:
            registry.unregister(name)
