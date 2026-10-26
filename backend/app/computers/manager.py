"""Backend-owned lifecycle for per-teammate computers (local + isolated Linux-dev)."""

from __future__ import annotations

import asyncio
import json
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
from .base import LIFECYCLE_STATES
from .billable_stub import BillableCloudNotConfigured
from .cloud_sandbox import ISOLATION_NOTE as CLOUD_SANDBOX_NOTE
from .cloud_sandbox import CloudSandboxComputer, CloudSandboxNotConfigured
from .context import current_computer_id, current_teammate_id
from .credit_policy import CreditPolicyError
from .daytona_client import DaytonaClientError, DaytonaSandboxClient
from .gcp_client import GcpClientError, GcpComputeClient
from .gcp_compute import ISOLATION_NOTE as GCP_ISOLATION_NOTE
from .gcp_compute import GcpComputeComputer
from .isolated_linux import ISOLATION_NOTE as ISOLATED_LINUX_NOTE
from .isolated_linux import IsolatedLinuxDevComputer
from .paths import computer_layout, default_computers_root, ensure_layout
from .workspace import WorkspaceError, WorkspaceStore

VALID_STATES = LIFECYCLE_STATES | frozenset({"error"})

SUPPORTED_PROVIDERS = frozenset(
    {"local_browser", "isolated_linux_dev", "gcp_compute", "cloud_sandbox", "billable_cloud"}
)

ISOLATION_NOTES = {
    "local_browser": (
        "Local persistent Playwright profile — not a full VM and not your everyday Chrome. "
        "Sessions may expire; re-authentication may be required."
    ),
    "isolated_linux_dev": ISOLATED_LINUX_NOTE,
    "gcp_compute": GCP_ISOLATION_NOTE,
    "cloud_sandbox": CLOUD_SANDBOX_NOTE,
    "billable_cloud": (
        "Billable managed cloud — not configured. Explicit provider approval + credentials required."
    ),
    "mock_cloud": "Contract-test mock only — not a real cloud computer.",
}


class ComputerError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class LocalBrowserComputer:
    """Concrete local provider: persistent Playwright profile + workspace."""

    provider_name = "local_browser"
    isolation_tier = "local_browser"

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

    async def provision(self) -> dict[str, Any]:
        ensure_layout(self.paths)
        return {
            "computer_id": self.record.id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "root": str(self.paths["root"]),
        }

    async def start(self, *, headed: bool = False) -> dict[str, Any]:
        await self.open_browser(headed=headed)
        return {"state": "ready", "browser_open": True, "healthy": True}

    async def stop(self) -> dict[str, Any]:
        await self.close_browser()
        return {"state": "stopped", "browser_open": False}

    async def health(self) -> dict[str, Any]:
        root_ok = self.paths["root"].is_dir()
        return {
            "healthy": root_ok,
            "root_ok": root_ok,
            "browser_process_alive": self.browser.is_open,
            "isolation_tier": self.isolation_tier,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def status(self) -> dict[str, Any]:
        return {
            "computer_id": self.record.id,
            "teammate_id": self.record.teammate_id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "browser_open": self.browser.is_open,
            "url": self.browser.last_url,
        }

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
            "control_state": "human_control",
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
            "control_state": "agent_control",
            "url": url,
            "note": "Browser state refreshed after human control. Prior page assumptions are invalid.",
        }

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ComputerError("Delete requires confirm=true — this removes workspace and profile data.")
        await self.close_browser()
        root = self.paths["root"]
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)
        return {"deleted": True, "computer_id": self.record.id, "root_removed": str(root)}

    def workspace_paths(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.paths.items()}


LiveComputer = LocalBrowserComputer | IsolatedLinuxDevComputer | GcpComputeComputer | CloudSandboxComputer


class ComputerManager:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        policy: NavigationPolicy,
        *,
        gcp_client: GcpComputeClient | None = None,
        daytona_client: DaytonaSandboxClient | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.policy = policy
        self.gcp_client = gcp_client
        self.daytona_client = daytona_client
        self.root = default_computers_root(settings.computers_root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, LiveComputer] = {}
        self._lock = asyncio.Lock()
        self._provision_locks: dict[int, asyncio.Lock] = {}
        if settings.gcp_credentials_file:
            # Path only — never log file contents.
            import os

            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", settings.gcp_credentials_file)

    def _normalize_provider(self, provider: str | None) -> str:
        name = (provider or self.settings.computer_default_provider or "local_browser").strip()
        if name not in SUPPORTED_PROVIDERS:
            raise ComputerError(
                f"Unsupported computer provider '{name}'. "
                f"Supported: local_browser, isolated_linux_dev, gcp_compute, cloud_sandbox "
                f"(billable_cloud remains refused without separate approval)."
            )
        if name == "billable_cloud":
            raise ComputerError(
                "billable_cloud refuses paid-resource creation. Use gcp_compute with "
                "credit-funded mode for the free-trial demo, cloud_sandbox stub for "
                "Daytona-style planning, or keep local providers."
            )
        return name

    def _isolation_tier_for(self, provider: str) -> str:
        return {
            "local_browser": "local_browser",
            "isolated_linux_dev": "filesystem_sandbox",
            "gcp_compute": "vm",
            "cloud_sandbox": "managed_sandbox",
            "billable_cloud": "managed_cloud",
        }.get(provider, "local_browser")

    def get_or_create_record(
        self, db: Session, teammate_id: int, *, provider: str | None = None
    ) -> ComputerRecord:
        if not db.get(Teammate, teammate_id):
            raise ComputerError("Teammate not found.")
        row = db.query(ComputerRecord).filter_by(teammate_id=teammate_id).first()
        if row:
            if provider and provider != row.provider:
                raise ComputerError(
                    f"Computer already exists with provider '{row.provider}'. "
                    "Delete it (confirm=true) before provisioning a different provider. "
                    "Local computers are never silently migrated to cloud."
                )
            return row
        chosen = self._normalize_provider(provider)
        computer_id = str(uuid.uuid4())
        paths = computer_layout(self.root, computer_id)
        ensure_layout(paths)
        row = ComputerRecord(
            id=computer_id,
            teammate_id=teammate_id,
            provider=chosen,
            isolation_tier=self._isolation_tier_for(chosen),
            control_state="agent_control",
            provider_meta_json="{}",
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

    def _build_live(self, record: ComputerRecord) -> LiveComputer:
        if record.provider == "isolated_linux_dev":
            return IsolatedLinuxDevComputer(record, self.settings, self.policy)
        if record.provider == "gcp_compute":
            return GcpComputeComputer(
                record,
                self.settings,
                client=self.gcp_client,
                session_factory=self.session_factory,
            )
        if record.provider == "cloud_sandbox":
            return CloudSandboxComputer(
                record,
                self.settings,
                client=self.daytona_client,
                session_factory=self.session_factory,
                nav_policy=self.policy,
            )
        if record.provider == "billable_cloud":
            raise ComputerError("Billable cloud provider is not configured.")
        return LocalBrowserComputer(record, self.settings, self.policy)

    async def _live_for(self, record: ComputerRecord) -> LiveComputer:
        async with self._lock:
            live = self._live.get(record.id)
            if live:
                return live
            live = self._build_live(record)
            self._live[record.id] = live
            return live

    def _provision_lock_for(self, teammate_id: int) -> asyncio.Lock:
        lock = self._provision_locks.get(teammate_id)
        if lock is None:
            lock = asyncio.Lock()
            self._provision_locks[teammate_id] = lock
        return lock

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

    def get_record(self, db: Session, teammate_id: int) -> ComputerRecord | None:
        return db.query(ComputerRecord).filter_by(teammate_id=teammate_id).first()

    def public_status(self, db: Session, teammate_id: int, *, create: bool = True) -> dict[str, Any]:
        record = self.get_or_create_record(db, teammate_id) if create else self.get_record(db, teammate_id)
        if record is None:
            raise ComputerError("Computer not found. Provision or start one for this teammate.")
        live = self._live.get(record.id)
        url = record.last_url
        if live and live.browser.is_open:
            url = live.browser.refresh_url_from_page() or url
        isolation_tier = getattr(record, "isolation_tier", None) or self._isolation_tier_for(record.provider)
        control_state = getattr(record, "control_state", None) or (
            "human_control" if record.human_control else "agent_control"
        )
        return {
            "id": record.id,
            "teammate_id": record.teammate_id,
            "provider": record.provider,
            "isolation_tier": isolation_tier,
            "control_state": control_state,
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
            "isolation_note": ISOLATION_NOTES.get(record.provider, ISOLATION_NOTES["local_browser"]),
            "limits": {
                "idle_stop_minutes": self.settings.computer_idle_stop_minutes,
                "max_runtime_minutes": self.settings.computer_max_runtime_minutes,
                "billable_allowed": self.settings.computer_allow_billable,
                "credit_funded_enabled": self.settings.computer_credit_funded_enabled,
                "cloud_sandbox_enabled": self.settings.computer_cloud_sandbox_enabled,
                "daytona_max_concurrent_sandboxes": self.settings.daytona_max_concurrent_sandboxes,
                "gcp_max_concurrent_vms": self.settings.gcp_max_concurrent_vms,
            },
        }

    def live_by_id(self, computer_id: str) -> LiveComputer | None:
        return self._live.get(computer_id)

    async def ensure_live(self, computer_id: str) -> LiveComputer:
        with self.session_factory() as db:
            record = db.get(ComputerRecord, computer_id)
            if not record:
                raise ComputerError("Computer not found.")
            return await self._live_for(record)

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

    async def provision(
        self,
        teammate_id: int,
        *,
        provider: str | None = None,
        confirm_estimate: bool = False,
    ) -> dict[str, Any]:
        """Idempotent provision: stable ID per teammate; never duplicates resources."""
        async with self._provision_lock_for(teammate_id):
            with self.session_factory() as db:
                existing = db.query(ComputerRecord).filter_by(teammate_id=teammate_id).first()
                if existing and provider and provider != existing.provider:
                    raise ComputerError(
                        f"Computer already exists with provider '{existing.provider}'. "
                        "Delete with confirm=true before changing providers."
                    )
                if existing and existing.state == "provisioning":
                    # Reconcile interrupted provision before another create.
                    existing.state = "stopped"
                    existing.last_action = "reconciled_interrupted_provision"
                    db.commit()
                record = self.get_or_create_record(db, teammate_id, provider=provider)
                self._set_state(db, record, "provisioning", last_error=None)
                computer_id = record.id
                chosen_provider = record.provider
            try:
                with self.session_factory() as db:
                    record = db.get(ComputerRecord, computer_id)
                    assert record is not None
                    live = await self._live_for(record)
                    if isinstance(live, (GcpComputeComputer, CloudSandboxComputer)):
                        info = await live.provision(confirm_estimate=confirm_estimate)
                    else:
                        info = await live.provision()
                    if info.get("requires_confirmation"):
                        self._set_state(
                            db,
                            record,
                            "stopped",
                            last_action="estimate_pending_confirmation",
                            control_state="agent_control",
                        )
                        status = self.public_status(db, teammate_id, create=False)
                        status["provision"] = info
                        status["requires_confirmation"] = True
                        status["estimated_cost"] = info.get("estimated_cost")
                        status["message"] = info.get("message")
                        return status
                    # Merge provider meta carefully — GCP/Daytona already persist on live
                    if isinstance(live, (GcpComputeComputer, CloudSandboxComputer)):
                        meta_json = live.record.provider_meta_json
                    else:
                        meta_json = json.dumps(
                            {"last_provision": info, "updated_at": datetime.now(timezone.utc).isoformat()}
                        )
                    self._set_state(
                        db,
                        record,
                        "stopped",
                        last_action="provision",
                        isolation_tier=getattr(live, "isolation_tier", record.isolation_tier),
                        provider_meta_json=meta_json,
                        external_ref=info.get("instance_name"),
                        control_state="agent_control",
                    )
                    status = self.public_status(db, teammate_id, create=False)
                    status["provision"] = info
                    status["idempotent"] = bool(info.get("idempotent"))
                    status["estimated_cost"] = info.get("estimated_cost")
                    return status
            except (
                BillableCloudNotConfigured,
                CreditPolicyError,
                GcpClientError,
                CloudSandboxNotConfigured,
                DaytonaClientError,
            ) as exc:
                reason = getattr(exc, "reason", str(exc))
                with self.session_factory() as db:
                    record = db.get(ComputerRecord, computer_id)
                    if record:
                        self._set_state(db, record, "failed", last_error=reason[:300])
                raise ComputerError(reason) from exc
            except Exception as exc:
                with self.session_factory() as db:
                    record = db.get(ComputerRecord, computer_id)
                    if record:
                        self._set_state(db, record, "failed", last_error=str(exc)[:300])
                raise ComputerError(f"Failed to provision computer: {exc}") from exc
            finally:
                _ = chosen_provider

    async def health(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_record(db, teammate_id)
            if record is None:
                raise ComputerError("Computer not found. Provision or start one for this teammate.")
            live = self._live.get(record.id)
            if live is None and record.provider in {"gcp_compute", "cloud_sandbox"}:
                # Always probe the remote API — filesystem presence is not sandbox/VM health.
                live = await self._live_for(record)
            if live is None:
                # Cheap filesystem health without starting browser processes.
                root = Path(record.root_path)
                probe = {
                    "healthy": root.is_dir(),
                    "root_ok": root.is_dir(),
                    "browser_process_alive": False,
                    "isolation_tier": record.isolation_tier,
                    "note": "Record exists; runtime not loaded. DB ready ≠ process alive.",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                probe = await live.health()
            status = self.public_status(db, teammate_id, create=False)
            status["health"] = probe
            return status

    async def delete_computer(self, teammate_id: int, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ComputerError("Deleting a computer requires confirm=true — workspace and profile are removed.")
        with self.session_factory() as db:
            record = db.query(ComputerRecord).filter_by(teammate_id=teammate_id).first()
            if not record:
                raise ComputerError("Computer not found.")
            computer_id = record.id
            self._set_state(db, record, "deleting", control_state="agent_control")
        live = self._live.pop(computer_id, None)
        try:
            if live is None:
                with self.session_factory() as db:
                    record = db.get(ComputerRecord, computer_id)
                    assert record is not None
                    live = self._build_live(record)
            info = await live.delete(confirm=True)
        except Exception as exc:
            with self.session_factory() as db:
                record = db.get(ComputerRecord, computer_id)
                if record:
                    self._set_state(db, record, "failed", last_error=f"Delete failed: {exc}"[:300])
            raise ComputerError(f"Failed to delete computer: {exc}") from exc
        with self.session_factory() as db:
            record = db.get(ComputerRecord, computer_id)
            if record:
                db.delete(record)
                db.commit()
        return {
            "deleted": True,
            "computer_id": computer_id,
            "teammate_id": teammate_id,
            "message": "Computer deleted. Workspace, profile, and artifacts under its root were removed.",
            "detail": info,
        }

    async def start(self, teammate_id: int, *, headed: bool = False) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            self._set_state(db, record, "starting", control_state="agent_control")
            computer_id = record.id
        try:
            with self.session_factory() as db:
                record = db.get(ComputerRecord, computer_id)
                assert record is not None
                live = await self._live_for(record)
                await live.open_browser(headed=headed)
                self._set_state(db, record, "ready", last_error=None, human_control=False, control_state="agent_control")
                return self.public_status(db, teammate_id)
        except Exception as exc:
            with self.session_factory() as db:
                record = db.get(ComputerRecord, computer_id)
                if record:
                    self._set_state(db, record, "failed", last_error=str(exc)[:300])
            raise ComputerError(f"Failed to start computer: {exc}") from exc

    async def stop(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            computer_id = record.id
            provider = record.provider
        live = self._live.pop(computer_id, None)
        if live is None and provider == "gcp_compute":
            with self.session_factory() as db:
                record = db.get(ComputerRecord, computer_id)
                assert record is not None
                live = self._build_live(record)
        if live is not None:
            if isinstance(live, GcpComputeComputer):
                try:
                    await live.stop()
                except GcpClientError as exc:
                    raise ComputerError(exc.reason) from exc
            else:
                await live.close_browser()
        with self.session_factory() as db:
            record = db.get(ComputerRecord, computer_id)
            assert record is not None
            self._set_state(db, record, "stopped", human_control=False, current_task=None)
            return self.public_status(db, teammate_id, create=False)

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
            self._set_state(db, record, "waiting_human", control_state="pause_requested")
            computer_id = record.id
        with self.session_factory() as db:
            record = db.get(ComputerRecord, computer_id)
            assert record is not None
            live = await self._live_for(record)
            self._set_state(db, record, "waiting_human", control_state="waiting_for_safe_point")
            # Wait for in-flight tool lock by acquiring action lock (safe point).
            async with live._action_lock:
                info = await live.enter_human_control()
            self._set_state(
                db,
                record,
                "human_control",
                human_control=True,
                control_state="human_control",
                last_url=info.get("url"),
                last_action="human_takeover",
            )
            status = self.public_status(db, teammate_id)
            status["takeover"] = info
            return status

    async def release_control(self, teammate_id: int) -> dict[str, Any]:
        with self.session_factory() as db:
            record = self.get_or_create_record(db, teammate_id)
            self._set_state(db, record, record.state, control_state="returning_to_agent")
            live = self._live.get(record.id)
            if not live:
                live = await self._live_for(record)
            info = await live.release_human_control()
            self._set_state(
                db,
                record,
                "ready" if live.browser.is_open else "stopped",
                human_control=False,
                control_state="agent_control",
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
            # gcp_compute / cloud_sandbox open remote desktop on demand; local needs browser up.
            if record.provider not in {"gcp_compute", "cloud_sandbox"} and not live.browser.is_open:
                raise ComputerError("Browser is not open. Start the computer first.")
            try:
                data = await live.preview()
            except (GcpClientError, CloudSandboxNotConfigured, DaytonaClientError) as exc:
                raise ComputerError(getattr(exc, "reason", str(exc))) from exc
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
                if record.state in {"provisioning", "deleting", "starting", "recovering"}:
                    record.state = "stopped"
                    record.last_action = "reconciled_interrupted_lifecycle"
                if record.human_control or record.state in {
                    "human_control",
                    "waiting_human",
                    "busy",
                    "starting",
                    "ready",
                    "paused",
                }:
                    record.human_control = False
                    if record.state not in {"failed", "error", "deleted"}:
                        record.state = "stopped"
                    record.last_action = "reconciled_on_startup"
                if hasattr(record, "control_state"):
                    record.control_state = "agent_control"
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
