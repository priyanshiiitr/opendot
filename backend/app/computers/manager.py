"""Backend-owned lifecycle for per-teammate local browser computers."""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..browser.policy import NavigationPolicy
from ..browser.session import BrowserService
from ..config import Settings
from ..database import ComputerRecord, Teammate
from .context import current_computer_id, current_teammate_id
from .paths import computer_layout, default_computers_root, ensure_layout
from .workspace import WorkspaceError, WorkspaceStore

VALID_STATES = frozenset(
    {
        "stopped",
        "starting",
        "ready",
        "busy",
        "paused",
        "waiting_human",
        "human_control",
        "recovering",
        "error",
    }
)


class ComputerError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class LocalBrowserComputer:
    """Concrete local provider: persistent Playwright profile + workspace."""

    provider_name = "local_browser"

    def __init__(
        self,
        record: ComputerRecord,
        settings: Settings,
        policy: NavigationPolicy,
    ):
        self.record = record
        self.settings = settings
        root = Path(record.root_path)
        self.paths = {
            "root": root,
            "profile": root / "profile",
            "workspace": root / "workspace",
            "artifacts": root / "artifacts",
            "metadata": root / "metadata",
            "preview": root / "artifacts" / "preview.png",
        }
        ensure_layout(self.paths)
        self.workspace = WorkspaceStore(
            self.paths["workspace"],
            self.paths["artifacts"],
            max_file_bytes=settings.computer_max_file_bytes,
        )
        self.browser = BrowserService(
            policy,
            headless=settings.computer_headless_default,
            navigation_timeout_ms=settings.browser_navigation_timeout_ms,
            max_actions=settings.browser_max_actions,
            max_extract_bytes=settings.browser_max_extract_bytes,
            artifact_dir=str(self.paths["artifacts"]),
            user_data_dir=self.paths["profile"],
            computer_id=record.id,
        )
        self._action_lock = asyncio.Lock()
        self._last_preview_at = 0.0

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]:
        self.browser.headless = not headed
        await self.browser.ensure_session()
        return {"opened": True, "headless": self.browser.headless, "url": self.browser.last_url}

    async def close_browser(self) -> dict[str, Any]:
        return await self.browser.close()

    async def preview(self) -> bytes:
        now = time.monotonic()
        min_interval = self.settings.computer_preview_min_interval_ms / 1000.0
        if now - self._last_preview_at < min_interval and self.paths["preview"].is_file():
            return self.paths["preview"].read_bytes()
        data = await self.browser.preview_bytes(max_width=self.settings.computer_preview_max_width)
        self.paths["preview"].write_bytes(data)
        self._last_preview_at = now
        return data

    async def enter_human_control(self) -> dict[str, Any]:
        # Pause agent actions; reopen headed on the same persistent profile if needed.
        self.browser.agent_blocked_reason = "Human control is active. Agent browser actions are paused."
        if self.browser.is_open and self.browser.headless:
            await self.browser.reopen(headless=False)
        elif not self.browser.is_open:
            await self.open_browser(headed=True)
        return {
            "human_control": True,
            "url": self.browser.refresh_url_from_page(),
            "note": "Use the headed AI Teammates browser window. This is not your everyday Chrome profile.",
        }

    async def release_human_control(self) -> dict[str, Any]:
        url = self.browser.refresh_url_from_page()
        self.browser.agent_blocked_reason = None
        # Return to default headless for background work unless user left it headed intentionally.
        if self.browser.is_open and not self.settings.computer_headless_default:
            pass
        elif self.browser.is_open and not self.browser.headless:
            await self.browser.reopen(headless=self.settings.computer_headless_default)
        return {
            "human_control": False,
            "url": url,
            "note": "Browser state refreshed after human control. Prior page assumptions are invalid.",
        }

    def workspace_paths(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.paths.items()}


class ComputerManager:
    def __init__(self, settings: Settings, session_factory: sessionmaker, policy: NavigationPolicy):
        self.settings = settings
        self.session_factory = session_factory
        self.policy = policy
        self.root = default_computers_root(settings.computers_root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, LocalBrowserComputer] = {}
        self._lock = asyncio.Lock()

    def get_or_create_record(self, db: Session, teammate_id: int) -> ComputerRecord:
        if not db.get(Teammate, teammate_id):
            raise ComputerError("Teammate not found.")
        row = db.query(ComputerRecord).filter_by(teammate_id=teammate_id).first()
        if row:
            return row
        computer_id = str(uuid.uuid4())
        paths = computer_layout(self.root, computer_id)
        ensure_layout(paths)
        row = ComputerRecord(
            id=computer_id,
            teammate_id=teammate_id,
            provider="local_browser",
            state="stopped",
            root_path=str(paths["root"]),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def assert_owner(self, db: Session, computer_id: str, teammate_id: int) -> ComputerRecord:
        row = db.get(ComputerRecord, computer_id)
        if not row or row.teammate_id != teammate_id:
            raise ComputerError("Computer not found for this teammate.")
        return row

    async def _live_for(self, record: ComputerRecord) -> LocalBrowserComputer:
        async with self._lock:
            live = self._live.get(record.id)
            if live:
                return live
            live = LocalBrowserComputer(record, self.settings, self.policy)
            self._live[record.id] = live
            return live

    def bind_context(self, teammate_id: int, computer_id: str):
        t = current_teammate_id.set(teammate_id)
        c = current_computer_id.set(computer_id)
        return t, c

    def reset_context(self, tokens) -> None:
        current_teammate_id.reset(tokens[0])
        current_computer_id.reset(tokens[1])

    async def session_for_teammate(self, teammate_id: int) -> BrowserService:
        """Resolve the browser session for tool calls (never from model-supplied IDs)."""
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            state = record.state
            human = record.human_control
            computer_id = record.id
            # Detach: load a fresh ORM instance inside _live_for via snapshot fields
            root_path = record.root_path
        # Reconstruct a lightweight record-like object for live cache keying
        with self.session_factory() as db:
            record = db.get(ComputerRecord, computer_id)
            assert record is not None
            live = await self._live_for(record)
        if human or state in {"human_control", "waiting_human", "paused"}:
            live.browser.agent_blocked_reason = (
                "Human control is active. Agent browser actions are paused."
                if human or state == "human_control"
                else f"Computer is {state}; agent browser actions are paused."
            )
        else:
            live.browser.agent_blocked_reason = None
        _ = root_path
        return live.browser

    def _record_snapshot(self, computer_id: str) -> ComputerRecord:
        with self.session_factory() as db:
            row = db.get(ComputerRecord, computer_id)
            if not row:
                raise ComputerError("Computer not found.")
            return row

    def workspace_for_teammate(self, teammate_id: int) -> WorkspaceStore:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            paths = computer_layout(self.root, record.id)
            # Prefer persisted root_path
            root = Path(record.root_path)
            return WorkspaceStore(root / "workspace", root / "artifacts", max_file_bytes=self.settings.computer_max_file_bytes)

    def public_status(self, db: Session, teammate_id: int) -> dict[str, Any]:
        record = self.get_or_create_record(db, teammate_id)
        live = self._live.get(record.id)
        url = record.last_url
        if live and live.browser.is_open:
            url = live.browser.refresh_url_from_page() or url
        return {
            "id": record.id,
            "teammate_id": record.teammate_id,
            "provider": record.provider,
            "state": record.state,
            "human_control": record.human_control,
            "last_url": url,
            "last_error": record.last_error,
            "last_action": record.last_action,
            "current_task": record.current_task,
            "workspace_path": str(Path(record.root_path) / "workspace"),
            "artifacts_path": str(Path(record.root_path) / "artifacts"),
            "browser_open": bool(live and live.browser.is_open),
            "last_used_at": record.last_used_at,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "isolation_note": (
                "Local persistent Playwright profile — not a full VM and not your everyday Chrome. "
                "Sessions may expire; re-authentication may be required."
            ),
        }

    def _set_state(self, db: Session, record: ComputerRecord, state: str, **fields: Any) -> ComputerRecord:
        if state not in VALID_STATES:
            raise ComputerError(f"Invalid computer state: {state}")
        record.state = state
        for key, value in fields.items():
            setattr(record, key, value)
        record.updated_at = datetime.now(timezone.utc)
        record.last_used_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(record)
        return record

    async def start(self, teammate_id: int, *, headed: bool = False) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            self._set_state(db, record, "starting")
            computer_id = record.id
        try:
            with self.session_factory() as db:
                record = db.get(ComputerRecord, computer_id)
                assert record is not None
                live = await self._live_for(record)
                await live.open_browser(headed=headed)
                self._set_state(db, record, "ready", last_error=None, human_control=False)
                return self.public_status(db, teammate_id)
        except Exception as exc:
            with self.session_factory() as db:
                record = db.get(ComputerRecord, computer_id)
                if record:
                    self._set_state(db, record, "error", last_error=str(exc)[:300])
            raise ComputerError(f"Failed to start computer: {exc}") from exc

    async def stop(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            live = self._live.pop(record.id, None)
            if live:
                await live.close_browser()
            self._set_state(db, record, "stopped", human_control=False, current_task=None)
            return self.public_status(db, teammate_id)

    async def pause(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            live = self._live.get(record.id)
            if live:
                live.browser.agent_blocked_reason = "Computer is paused."
            self._set_state(db, record, "paused")
            return self.public_status(db, teammate_id)

    async def resume(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            if record.human_control:
                raise ComputerError("Release human control before resuming the agent.")
            live = self._live.get(record.id)
            if live:
                live.browser.agent_blocked_reason = None
            self._set_state(db, record, "ready" if (live and live.browser.is_open) else "stopped")
            return self.public_status(db, teammate_id)

    async def takeover(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            self._set_state(db, record, "waiting_human")
            computer_id = record.id
        with self.session_factory() as db:
            record = db.get(ComputerRecord, computer_id)
            assert record is not None
            live = await self._live_for(record)
            # Wait briefly for in-flight tool lock by acquiring action lock.
            async with live._action_lock:
                info = await live.enter_human_control()
            self._set_state(
                db,
                record,
                "human_control",
                human_control=True,
                last_url=info.get("url"),
                last_action="human_takeover",
            )
            status = self.public_status(db, teammate_id)
            status["takeover"] = info
            return status

    async def release_control(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            live = self._live.get(record.id)
            if not live:
                live = await self._live_for(record)
            info = await live.release_human_control()
            self._set_state(
                db,
                record,
                "ready" if live.browser.is_open else "stopped",
                human_control=False,
                last_url=info.get("url"),
                last_action="release_human_control",
                last_error=None,
            )
            status = self.public_status(db, teammate_id)
            status["release"] = info
            return status

    async def preview(self, teammate_id: int) -> bytes:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            live = await self._live_for(record)
            if not live.browser.is_open:
                raise ComputerError("Browser is not open. Start the computer first.")
            data = await live.preview()
            record.preview_path = str(live.paths["preview"])
            db.commit()
            return data

    async def clear_browser_data(self, teammate_id: int, *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise ComputerError("Clearing browser data requires confirm=true.")
        await self.stop(teammate_id)
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            profile = Path(record.root_path) / "profile"
            if profile.exists():
                shutil.rmtree(profile)
            profile.mkdir(parents=True, exist_ok=True)
            self._set_state(db, record, "stopped", last_url=None, last_error=None, last_action="clear_browser_data")
            return {
                **self.public_status(db, teammate_id),
                "cleared": True,
                "message": "Browser profile deleted. Workspace files were kept. Site logins in this computer are gone.",
            }

    def list_workspace(self, teammate_id: int, area: str = "workspace") -> list[dict[str, Any]]:
        return self.workspace_for_teammate(teammate_id).list_files(area=area)

    def read_workspace_file(self, teammate_id: int, path: str, area: str = "workspace") -> dict[str, Any]:
        try:
            return self.workspace_for_teammate(teammate_id).read_text(path, area=area)
        except WorkspaceError as exc:
            raise ComputerError(exc.reason) from exc

    def file_bytes(self, teammate_id: int, path: str, area: str = "artifacts") -> tuple[Path, bytes]:
        try:
            abs_path = self.workspace_for_teammate(teammate_id).absolute_for_download(path, area=area)
            return abs_path, abs_path.read_bytes()
        except WorkspaceError as exc:
            raise ComputerError(exc.reason) from exc

    def reconcile_on_startup(self) -> None:
        """DB may say ready while no process exists — mark stopped; never restore human_control."""
        with self.session_factory() as db:
            for record in db.query(ComputerRecord).all():
                if record.human_control or record.state in {"human_control", "waiting_human", "busy", "starting", "ready", "paused"}:
                    record.human_control = False
                    if record.state != "error":
                        record.state = "stopped"
                    record.last_action = "reconciled_on_startup"
                    record.updated_at = datetime.now(timezone.utc)
            db.commit()

    async def shutdown(self) -> None:
        for computer_id, live in list(self._live.items()):
            try:
                await live.close_browser()
            except Exception:
                pass
            self._live.pop(computer_id, None)
        with self.session_factory() as db:
            for record in db.query(ComputerRecord).all():
                if record.state not in {"stopped", "error"}:
                    record.state = "stopped"
                record.human_control = False
            db.commit()

    def mark_busy(self, teammate_id: int, task: str | None) -> None:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            if record.state not in {"human_control", "waiting_human", "paused"}:
                self._set_state(db, record, "busy", current_task=task)

    def mark_ready(self, teammate_id: int) -> None:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            if record.human_control:
                return
            if record.state == "busy":
                self._set_state(db, record, "ready" if self._live.get(record.id) and self._live[record.id].browser.is_open else "stopped", current_task=None)
