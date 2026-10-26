"""Managed cloud sandbox provider — Daytona adapter (credit/free-tier path).

Requires AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED=true, a Daytona API key,
and confirm_estimate=true before creating a remote sandbox.
Billable/PAYG remains blocked separately (ALLOW_BILLABLE must stay false).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..database import ComputerRecord
from .daytona_client import DaytonaClientError, DaytonaSandboxClient, build_daytona_client
from .paths import ensure_layout
from .workspace import WorkspaceStore

ISOLATION_NOTE = (
    "cloud_sandbox: Daytona managed per-teammate sandbox (workspace/process isolation). "
    "Sandbox is not public (public=False). Desktop via Daytona computer_use; "
    "Playwright browser_* tools remain local_browser. Secrets stay on the control plane. "
    "ALLOW_BILLABLE remains false."
)

DAYTONA_ADAPTER_METHODS = (
    "provision",
    "start",
    "stop",
    "health",
    "open_browser",
    "close_browser",
    "status",
    "preview",
    "enter_human_control",
    "release_human_control",
    "delete",
    "workspace_paths",
)


class CloudSandboxNotConfigured(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _sandbox_name(computer_id: str) -> str:
    short = computer_id.replace("-", "")[:20]
    return f"aitm-{short}"


def build_cloud_sandbox_estimate(settings: Settings) -> dict[str, Any]:
    return {
        "provider": "cloud_sandbox",
        "vendor": "daytona",
        "isolation_tier": "managed_sandbox",
        "usd_per_hour_approx": None,
        "is_guarantee": False,
        "notes": [
            "Daytona wallet: free credits consumed before paid balance.",
            "GPU sandboxes are not used on this path.",
            "AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE remains false.",
            f"Idle auto-stop ~{settings.computer_idle_stop_minutes} minutes.",
            f"Max concurrent sandboxes: {settings.daytona_max_concurrent_sandboxes}.",
        ],
        "adapter_methods": list(DAYTONA_ADAPTER_METHODS),
    }


def assert_cloud_sandbox_allowed(
    settings: Settings, db: Session, *, exclude_computer_id: str | None = None
) -> dict[str, Any]:
    if settings.computer_allow_billable:
        raise CloudSandboxNotConfigured(
            "Refusing cloud_sandbox while AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE=true. "
            "Out-of-pocket PAYG is not authorized on this path."
        )
    if not settings.computer_cloud_sandbox_enabled:
        raise CloudSandboxNotConfigured(
            "cloud_sandbox disabled. Set AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED=true "
            "only after a Daytona API key is configured locally. No resources were created."
        )
    # Count provisioned Daytona computers
    q = db.query(ComputerRecord).filter(
        ComputerRecord.provider == "cloud_sandbox",
        ComputerRecord.state.notin_(["failed", "deleted"]),
    )
    if exclude_computer_id:
        q = q.filter(ComputerRecord.id != exclude_computer_id)
    active = 0
    for row in q.all():
        try:
            meta = json.loads(row.provider_meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        if meta.get("provisioned") and meta.get("sandbox_id"):
            active += 1
    limit = max(1, int(settings.daytona_max_concurrent_sandboxes))
    if active >= limit:
        raise CloudSandboxNotConfigured(
            f"cloud_sandbox concurrency limit reached ({limit}). "
            "Delete or stop an existing sandbox before provisioning another."
        )
    return build_cloud_sandbox_estimate(settings)


class CloudSandboxComputer:
    provider_name = "cloud_sandbox"
    isolation_tier = "managed_sandbox"

    def __init__(
        self,
        record: ComputerRecord,
        settings: Settings,
        client: DaytonaSandboxClient | None = None,
        *,
        session_factory=None,
        nav_policy=None,
    ):
        self.record = record
        self.settings = settings
        self.client = client or build_daytona_client(
            api_key=settings.daytona_api_key,
            api_url=settings.daytona_api_url,
            target=settings.daytona_target,
        )
        self.session_factory = session_factory
        self._nav_policy = nav_policy
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
        from .daytona_browser import DaytonaBrowserAdapter

        self.browser = DaytonaBrowserAdapter(self)
        import asyncio

        self._action_lock = asyncio.Lock()
        self._computer_use_started = False
        self._last_preview_at = 0.0

    def _meta(self) -> dict[str, Any]:
        try:
            return json.loads(self.record.provider_meta_json or "{}")
        except json.JSONDecodeError:
            return {}

    def _save_meta(self, meta: dict[str, Any]) -> None:
        self.record.provider_meta_json = json.dumps(meta)
        if self.session_factory is not None:
            with self.session_factory() as db:
                row = db.get(ComputerRecord, self.record.id)
                if row:
                    row.provider_meta_json = self.record.provider_meta_json
                    row.external_ref = meta.get("sandbox_id")
                    db.commit()

    async def provision(self, *, confirm_estimate: bool = False) -> dict[str, Any]:
        if self.session_factory is None:
            raise CloudSandboxNotConfigured("session_factory required for cloud_sandbox policy checks")
        with self.session_factory() as db:
            estimate = assert_cloud_sandbox_allowed(
                self.settings, db, exclude_computer_id=self.record.id
            )
        if not confirm_estimate:
            return {
                "requires_confirmation": True,
                "estimated_cost": estimate,
                "computer_id": self.record.id,
                "provider": self.provider_name,
                "created": False,
                "message": (
                    "Review the Daytona free/credit estimate, then re-submit with "
                    "confirm_estimate=true to create at most one sandbox."
                ),
            }
        if not self.client.credentials_ready():
            raise CloudSandboxNotConfigured(
                "Daytona API key not found. Set DAYTONA_API_KEY (or AI_TEAMMATES_DAYTONA_API_KEY) "
                "in backend/.env — never commit the key. No sandbox was created."
            )
        meta = self._meta()
        if meta.get("sandbox_id") and meta.get("provisioned"):
            return {
                "computer_id": self.record.id,
                "provider": self.provider_name,
                "idempotent": True,
                "sandbox_id": meta["sandbox_id"],
                "estimated_cost": estimate,
                "created": False,
            }
        name = _sandbox_name(self.record.id)
        labels = {
            "ai-teammates": "true",
            "computer-id": self.record.id[:63],
            "teammate-id": str(self.record.teammate_id),
        }
        try:
            info = await self.client.create_sandbox(
                name=name,
                labels=labels,
                auto_stop_minutes=self.settings.computer_idle_stop_minutes,
                public=False,
            )
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        meta.update(
            {
                "provisioned": True,
                "vendor": "daytona",
                "sandbox_id": info.id,
                "sandbox_name": info.name or name,
                "state": info.state,
                "estimated_cost": estimate,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "idle_stop_minutes": self.settings.computer_idle_stop_minutes,
            }
        )
        self._save_meta(meta)
        (self.paths["metadata"] / "daytona.json").write_text(
            json.dumps({k: v for k, v in meta.items() if k != "estimated_cost"}, indent=2),
            encoding="utf-8",
        )
        return {
            "computer_id": self.record.id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "sandbox_id": info.id,
            "instance_name": info.id,
            "status": info.state,
            "estimated_cost": estimate,
            "created": True,
            "note": ISOLATION_NOTE,
            "security": "Sandbox is not public; no VNC/debug ports exposed by this adapter.",
        }

    async def start(self, *, headed: bool = False) -> dict[str, Any]:
        meta = self._meta()
        sid = meta.get("sandbox_id")
        if not sid:
            raise CloudSandboxNotConfigured("Sandbox not provisioned.")
        try:
            info = await self.client.start_sandbox(sid)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        meta["state"] = info.state
        self._save_meta(meta)
        desktop = await self.open_browser(headed=headed)
        return {
            "state": "ready" if info.state == "started" else info.state,
            "status": info.state,
            "desktop": desktop,
        }

    async def stop(self) -> dict[str, Any]:
        meta = self._meta()
        sid = meta.get("sandbox_id")
        if not sid:
            return {"state": "stopped", "note": "no remote sandbox"}
        try:
            info = await self.client.stop_sandbox(sid)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        meta["state"] = info.state
        self._save_meta(meta)
        return {"state": "stopped", "status": info.state}

    async def health(self) -> dict[str, Any]:
        meta = self._meta()
        sid = meta.get("sandbox_id")
        configured = self.settings.computer_cloud_sandbox_enabled and self.client.credentials_ready()
        if not sid:
            return {
                "healthy": False,
                "configured": configured,
                "implemented": True,
                "vendor": "daytona",
                "isolation_tier": self.isolation_tier,
                "note": "No sandbox provisioned yet.",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        try:
            info = await self.client.get_sandbox(sid)
            healthy = info.state == "started"
            return {
                "healthy": healthy,
                "configured": configured,
                "implemented": True,
                "vendor": "daytona",
                "sandbox_id": sid,
                "status": info.state,
                "isolation_tier": self.isolation_tier,
                "note": ISOLATION_NOTE,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except DaytonaClientError as exc:
            return {
                "healthy": False,
                "configured": configured,
                "implemented": True,
                "error": exc.reason,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed  # Daytona desktop is remote; no local headed Chromium.
        meta = self._meta()
        sid = meta.get("sandbox_id")
        if not sid:
            raise CloudSandboxNotConfigured("Sandbox not provisioned.")
        try:
            # Ensure sandbox is up, then start desktop stack (Xvfb/xfce inside Daytona).
            info = await self.client.get_sandbox(sid)
            if info.state != "started":
                info = await self.client.start_sandbox(sid)
                meta["state"] = info.state
                self._save_meta(meta)
            cu = await self.client.start_computer_use(sid)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        self._computer_use_started = True
        # DaytonaBrowserAdapter.is_open reads computer_use flag via ensure path
        return {
            "opened": True,
            "computer_use": True,
            "message": cu.get("message"),
            "note": (
                "Daytona computer_use desktop started inside a non-public sandbox. "
                "browser_open/screenshot map here; CSS click/type need desktop_* coordinates."
            ),
            "security": "Sandbox public=False; no public VNC URL is published by this adapter.",
        }

    async def close_browser(self) -> dict[str, Any]:
        meta = self._meta()
        sid = meta.get("sandbox_id")
        if sid and self._computer_use_started:
            try:
                await self.client.stop_computer_use(sid)
            except DaytonaClientError:
                pass
        self._computer_use_started = False
        return {"closed": True, "computer_use": False}

    async def status(self) -> dict[str, Any]:
        meta = self._meta()
        return {
            "computer_id": self.record.id,
            "teammate_id": self.record.teammate_id,
            "provider": self.provider_name,
            "vendor": "daytona",
            "isolation_tier": self.isolation_tier,
            "isolation_note": ISOLATION_NOTE,
            "sandbox_id": meta.get("sandbox_id"),
            "adapter_methods": list(DAYTONA_ADAPTER_METHODS),
        }

    async def _require_desktop(self) -> str:
        meta = self._meta()
        sid = meta.get("sandbox_id")
        if not sid:
            raise CloudSandboxNotConfigured("Sandbox not provisioned.")
        if not self._computer_use_started:
            await self.open_browser()
        return sid

    async def desktop_click(self, *, x: int, y: int, button: str = "left", double: bool = False) -> dict[str, Any]:
        sid = await self._require_desktop()
        try:
            result = await self.client.mouse_click(sid, x=x, y=y, button=button, double=double)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        return {"action": "desktop_click", **result}

    async def desktop_type(self, *, text: str) -> dict[str, Any]:
        sid = await self._require_desktop()
        try:
            result = await self.client.keyboard_type(sid, text=text)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        return {"action": "desktop_type", **result}

    async def desktop_hotkey(self, *, keys: str) -> dict[str, Any]:
        sid = await self._require_desktop()
        try:
            result = await self.client.keyboard_hotkey(sid, keys=keys)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        return {"action": "desktop_hotkey", **result}

    async def desktop_open_url(self, *, url: str) -> dict[str, Any]:
        """Open a policy-checked http(s) URL in Chromium inside the sandbox desktop."""
        import shlex

        sid = await self._require_desktop()
        quoted = shlex.quote(url)
        # Prefer Chromium; fall back to xdg-open. No public ports published by this adapter.
        cmd = (
            f"(command -v chromium-browser >/dev/null && nohup chromium-browser --no-sandbox "
            f"--disable-dev-shm-usage --disable-gpu {quoted} >/tmp/aitm-browser.log 2>&1 &) || "
            f"(command -v chromium >/dev/null && nohup chromium --no-sandbox "
            f"--disable-dev-shm-usage --disable-gpu {quoted} >/tmp/aitm-browser.log 2>&1 &) || "
            f"(command -v google-chrome >/dev/null && nohup google-chrome --no-sandbox "
            f"--disable-dev-shm-usage --disable-gpu {quoted} >/tmp/aitm-browser.log 2>&1 &) || "
            f"xdg-open {quoted}"
        )
        try:
            out = await self.client.exec_command(sid, cmd)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        self.browser.last_url = url
        return {
            "action": "desktop_open_url",
            "url": url,
            "exit_code": out.get("exit_code"),
            "note": "Opened inside non-public Daytona desktop; use desktop_screenshot / desktop_click next.",
        }

    async def preview(self) -> bytes:
        import time

        meta = self._meta()
        sid = meta.get("sandbox_id")
        if not sid:
            raise CloudSandboxNotConfigured("Sandbox not provisioned.")
        if not self._computer_use_started:
            await self.open_browser()
        now = time.monotonic()
        min_interval = self.settings.computer_preview_min_interval_ms / 1000.0
        if now - self._last_preview_at < min_interval and self.paths["preview"].is_file():
            return self.paths["preview"].read_bytes()
        try:
            data = await self.client.screenshot_png(sid)
        except DaytonaClientError as exc:
            raise CloudSandboxNotConfigured(exc.reason) from exc
        self.paths["preview"].write_bytes(data)
        self._last_preview_at = now
        return data

    async def enter_human_control(self) -> dict[str, Any]:
        return {
            "human_control": True,
            "note": "Control plane marks human_control; remote Daytona desktop takeover UI is not wired.",
        }

    async def release_human_control(self) -> dict[str, Any]:
        return {"human_control": False}

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise CloudSandboxNotConfigured("Delete requires confirm=true.")
        meta = self._meta()
        sid = meta.get("sandbox_id")
        remote_deleted = False
        if sid and self.client.credentials_ready():
            try:
                await self.client.delete_sandbox(sid)
                remote_deleted = True
            except DaytonaClientError as exc:
                raise CloudSandboxNotConfigured(exc.reason) from exc
        import shutil

        if self.paths["root"].is_dir():
            shutil.rmtree(self.paths["root"], ignore_errors=True)
        meta["provisioned"] = False
        meta["state"] = "destroyed"
        self._save_meta(meta)
        return {
            "deleted": True,
            "computer_id": self.record.id,
            "remote_deleted": remote_deleted,
            "sandbox_id": sid,
        }

    def workspace_paths(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.paths.items()}


# Browser session for cloud_sandbox is DaytonaBrowserAdapter (see daytona_browser.py).
