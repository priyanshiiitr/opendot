"""Telegram bot connector — Bot API only, not a personal user account."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...database import ConnectorRecord
from ...tools import ToolError, ToolRegistry
from ..base import Connector, ConnectorCapability, ConnectorStatus
from ..credentials import CredentialStore
from .adapter import TelegramAPIError, TelegramBotAPI
from .tools import TELEGRAM_TOOL_NAMES, register_telegram_tools

TOKEN_KEY = "telegram.bot_token"

CAPABILITIES = [
    ConnectorCapability("telegram_get_bot_info", "Read verified bot identity via getMe", "read_only"),
    ConnectorCapability("telegram_get_recent_updates", "Read a bounded set of recent bot updates", "read_only"),
    ConnectorCapability("telegram_send_message", "Send a text message (requires approval)", "approval_required"),
]


class TelegramConnector(Connector):
    id = "telegram"
    display_name = "Telegram Bot"

    def __init__(self, store: CredentialStore, db_factory, http_client_factory=None):
        self._store = store
        self._db_factory = db_factory
        self._http_client_factory = http_client_factory
        self._api: TelegramBotAPI | None = None

    def _record(self, db: Session) -> ConnectorRecord:
        row = db.get(ConnectorRecord, self.id)
        if row is None:
            row = ConnectorRecord(id=self.id, enabled=False, config_json="{}")
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def _config(self, row: ConnectorRecord) -> dict[str, Any]:
        try:
            data = json.loads(row.config_json or "{}")
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return data

    def _save_config(self, db: Session, row: ConnectorRecord, config: dict[str, Any], *, enabled: bool | None = None) -> None:
        # Never persist tokens in SQLite.
        safe = {k: v for k, v in config.items() if k != "token"}
        row.config_json = json.dumps(safe, ensure_ascii=False)
        if enabled is not None:
            row.enabled = enabled
        db.commit()

    def _token(self) -> str | None:
        return self._store.get_secret(TOKEN_KEY)

    def _build_api(self, token: str) -> TelegramBotAPI:
        client = self._http_client_factory() if self._http_client_factory else None
        return TelegramBotAPI(token, client=client)

    def status(self) -> ConnectorStatus:
        with self._db_factory() as db:
            row = self._record(db)
            cfg = self._config(row)
            token_present = bool(self._token())
            connected = bool(row.enabled and token_present and cfg.get("bot_username"))
            return ConnectorStatus(
                id=self.id,
                display_name=self.display_name,
                connected=connected,
                healthy=connected,
                credentials_writable=self._store.writable,
                bot_username=cfg.get("bot_username"),
                bot_id=cfg.get("bot_id"),
                chat_allowlist=list(cfg.get("chat_allowlist") or []),
                capabilities=list(CAPABILITIES),
                message=cfg.get("last_error") or (
                    "Connected."
                    if connected
                    else "Not connected. Add a BotFather token in Connections."
                ),
                token_configured=token_present,
            )

    async def verify(self) -> ConnectorStatus:
        token = self._token()
        if not token:
            status = self.status()
            status.healthy = False
            status.message = "No bot token is stored."
            return status
        try:
            api = self._build_api(token)
            me = await api.get_me()
        except TelegramAPIError as exc:
            with self._db_factory() as db:
                row = self._record(db)
                cfg = self._config(row)
                cfg["last_error"] = str(exc)
                self._save_config(db, row, cfg, enabled=False)
            status = self.status()
            status.connected = False
            status.healthy = False
            status.message = str(exc)
            return status
        with self._db_factory() as db:
            row = self._record(db)
            cfg = self._config(row)
            cfg["bot_username"] = me.get("username")
            cfg["bot_id"] = me.get("id")
            cfg["last_error"] = ""
            self._save_config(db, row, cfg, enabled=True)
        return self.status()

    async def configure(self, token: str, chat_allowlist: list[int]) -> ConnectorStatus:
        if not self._store.writable:
            raise RuntimeError(
                "Secure credential storage is unavailable. On Windows, enable Windows Credential Manager "
                "(keyring WinVaultKeyring). Plaintext token storage is disabled."
            )
        try:
            api = self._build_api(token.strip())
            me = await api.get_me()
        except TelegramAPIError as exc:
            raise RuntimeError(str(exc)) from exc
        self._store.set_secret(TOKEN_KEY, token.strip())
        allowlist = sorted({int(x) for x in chat_allowlist})
        with self._db_factory() as db:
            row = self._record(db)
            cfg = self._config(row)
            cfg["bot_username"] = me.get("username")
            cfg["bot_id"] = me.get("id")
            cfg["chat_allowlist"] = allowlist
            cfg["last_update_offset"] = cfg.get("last_update_offset")
            cfg["last_error"] = ""
            self._save_config(db, row, cfg, enabled=True)
        return self.status()

    async def disconnect(self) -> ConnectorStatus:
        self._store.delete_secret(TOKEN_KEY)
        with self._db_factory() as db:
            row = self._record(db)
            self._save_config(
                db,
                row,
                {"bot_username": None, "bot_id": None, "chat_allowlist": [], "last_update_offset": None, "last_error": ""},
                enabled=False,
            )
        return self.status()

    def register_tools(self, registry: ToolRegistry) -> None:
        self.unregister_tools(registry)
        if not self.status().connected:
            return
        register_telegram_tools(registry, self)

    def unregister_tools(self, registry: ToolRegistry) -> None:
        for name in TELEGRAM_TOOL_NAMES:
            registry.unregister(name)

    def allowlist(self) -> set[int]:
        return set(self.status().chat_allowlist)

    def get_offset(self) -> int | None:
        with self._db_factory() as db:
            cfg = self._config(self._record(db))
            value = cfg.get("last_update_offset")
            return int(value) if value is not None else None

    def set_offset(self, offset: int) -> None:
        with self._db_factory() as db:
            row = self._record(db)
            cfg = self._config(row)
            cfg["last_update_offset"] = offset
            self._save_config(db, row, cfg)

    async def api(self) -> TelegramBotAPI:
        token = self._token()
        if not token:
            raise ToolError("Telegram bot is not configured.")
        return self._build_api(token)
