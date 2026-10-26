"""Lifecycle manager for configured connectors and their tool registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Settings, get_settings
from ..tools import ToolRegistry
from .base import Connector, ConnectorStatus
from .credentials import CredentialStore
from .oauth import GmailOAuthConnector, GitHubOAuthConnector
from .telegram.connector import TelegramConnector


class ConnectorManager:
    def __init__(
        self,
        store: CredentialStore,
        db_factory: Callable,
        registry: ToolRegistry,
        settings: Settings | None = None,
    ):
        self.store = store
        self.registry = registry
        self.settings = settings or get_settings()
        self._connectors: dict[str, Connector] = {
            "telegram": TelegramConnector(store, db_factory),
            "github": GitHubOAuthConnector(store, self.settings),
            "gmail": GmailOAuthConnector(store, self.settings),
        }
        self.refresh_tools()

    def list_statuses(self) -> list[dict[str, Any]]:
        return [connector.status().public_dict() for connector in self._connectors.values()]

    def get(self, connector_id: str) -> Connector:
        connector = self._connectors.get(connector_id)
        if connector is None:
            raise KeyError(connector_id)
        return connector

    def status(self, connector_id: str) -> ConnectorStatus:
        return self.get(connector_id).status()

    def refresh_tools(self) -> None:
        for connector in self._connectors.values():
            connector.unregister_tools(self.registry)
            connector.register_tools(self.registry)

    async def configure_telegram(self, token: str, chat_allowlist: list[int]) -> dict[str, Any]:
        telegram = self.get("telegram")
        status = await telegram.configure(token, chat_allowlist)
        self.refresh_tools()
        return status.public_dict()

    async def verify_telegram(self) -> dict[str, Any]:
        status = await self.get("telegram").verify()
        self.refresh_tools()
        return status.public_dict()

    async def disconnect_telegram(self) -> dict[str, Any]:
        status = await self.get("telegram").disconnect()
        self.refresh_tools()
        return status.public_dict()
