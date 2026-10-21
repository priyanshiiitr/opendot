"""Provider-neutral connector contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ConnectorCapability:
    name: str
    description: str
    permission: Literal["read_only", "approval_required"]


@dataclass
class ConnectorStatus:
    id: str
    display_name: str
    connected: bool
    healthy: bool
    credentials_writable: bool
    bot_username: str | None = None
    bot_id: int | None = None
    chat_allowlist: list[int] = field(default_factory=list)
    capabilities: list[ConnectorCapability] = field(default_factory=list)
    message: str = ""
    token_configured: bool = False
    # Never include secrets here.

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "connected": self.connected,
            "healthy": self.healthy,
            "credentials_writable": self.credentials_writable,
            "bot_username": self.bot_username,
            "bot_id": self.bot_id,
            "chat_allowlist": list(self.chat_allowlist),
            "capabilities": [
                {"name": c.name, "description": c.description, "permission": c.permission} for c in self.capabilities
            ],
            "message": self.message,
            "token_configured": self.token_configured,
        }


class Connector(ABC):
    id: str
    display_name: str

    @abstractmethod
    def status(self) -> ConnectorStatus: ...

    @abstractmethod
    async def verify(self) -> ConnectorStatus: ...

    @abstractmethod
    async def configure(self, token: str, chat_allowlist: list[int]) -> ConnectorStatus: ...

    @abstractmethod
    async def disconnect(self) -> ConnectorStatus: ...

    @abstractmethod
    def register_tools(self, registry) -> None: ...

    @abstractmethod
    def unregister_tools(self, registry) -> None: ...
