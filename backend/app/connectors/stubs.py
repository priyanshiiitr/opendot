"""Gmail / GitHub connectors — OAuth-gated scaffolding (refuse until credentials + enable)."""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import Connector, ConnectorCapability, ConnectorStatus


class FutureConnectorStub(Connector):
    """Honest placeholder: visible in Connections UI; never stores secrets in this module."""

    def __init__(
        self,
        connector_id: str,
        display_name: str,
        *,
        next_step: str,
        client_id: str = "",
        client_secret: str = "",
        oauth_enabled: bool = False,
    ):
        self.id = connector_id
        self.display_name = display_name
        self._next_step = next_step
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self._oauth_enabled = oauth_enabled

    @property
    def _creds_present(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def status(self) -> ConnectorStatus:
        ready = self._oauth_enabled and self._creds_present
        return ConnectorStatus(
            id=self.id,
            display_name=self.display_name,
            connected=False,
            healthy=False,
            credentials_writable=False,
            capabilities=[
                ConnectorCapability(
                    name=f"{self.id}_stub",
                    description=f"{self.display_name} OAuth tools are not live until a real adapter is completed.",
                    permission="approval_required",
                )
            ],
            message=(
                f"{self.display_name}: oauth_enabled={self._oauth_enabled}, "
                f"client_id_set={bool(self._client_id)}, client_secret_set={bool(self._client_secret)}. "
                + (
                    "Credentials present but OAuth token exchange / tools are not implemented yet — "
                    f"{self._next_step}"
                    if ready
                    else f"Refuses work until AI_TEAMMATES_OAUTH_CONNECTORS_ENABLED=true and "
                    f"AI_TEAMMATES_{self.id.upper()}_OAUTH_CLIENT_ID/SECRET are set. {self._next_step}"
                )
            ),
            token_configured=self._creds_present,
        )

    async def verify(self) -> ConnectorStatus:
        return self.status()

    async def configure(self, token: str, chat_allowlist: list[int]) -> ConnectorStatus:
        _ = token, chat_allowlist
        status = self.status()
        if not self._oauth_enabled:
            status.message = (
                f"Refusing to configure {self.display_name}: set "
                "AI_TEAMMATES_OAUTH_CONNECTORS_ENABLED=true after creating an OAuth app."
            )
            return status
        if not self._creds_present:
            status.message = (
                f"Refusing to configure {self.display_name}: OAuth client id/secret missing in env. "
                "Never paste secrets in chat."
            )
            return status
        status.message = (
            f"{self.display_name} OAuth client is configured in env, but the authorization-code "
            f"exchange and API tools are not implemented yet. {self._next_step}"
        )
        return status

    async def disconnect(self) -> ConnectorStatus:
        return self.status()

    def register_tools(self, registry) -> None:
        _ = registry

    def unregister_tools(self, registry) -> None:
        _ = registry


def github_stub(settings: Settings | None = None) -> FutureConnectorStub:
    s = settings or get_settings()
    return FutureConnectorStub(
        "github",
        "GitHub",
        next_step="Implement GitHub App / OAuth code exchange + scoped tools in a later PR.",
        client_id=s.github_oauth_client_id,
        client_secret=s.github_oauth_client_secret,
        oauth_enabled=s.oauth_connectors_enabled,
    )


def gmail_stub(settings: Settings | None = None) -> FutureConnectorStub:
    s = settings or get_settings()
    return FutureConnectorStub(
        "gmail",
        "Gmail",
        next_step="Implement Google OAuth code exchange + Gmail tools (never paste refresh tokens in chat).",
        client_id=s.gmail_oauth_client_id,
        client_secret=s.gmail_oauth_client_secret,
        oauth_enabled=s.oauth_connectors_enabled,
    )
