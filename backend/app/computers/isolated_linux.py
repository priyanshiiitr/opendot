"""Filesystem-isolated Linux-dev computer (local testing).

Honest scope:
- Separate root, workspace, and Playwright profile per computer ID.
- Not a hypervisor VM; not billable cloud.
- Optional Docker path is documented but not required for contract tests.
- Does not receive control-plane credentials or host .env secrets.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..browser.policy import NavigationPolicy
from ..browser.session import BrowserService
from ..config import Settings
from ..database import ComputerRecord
from .paths import ensure_layout
from .workspace import WorkspaceStore

ISOLATION_NOTE = (
    "isolated_linux_dev: filesystem + browser-profile sandbox on the host. "
    "Not a security-equivalent VM. Billable cloud VMs require an approved provider + credentials."
)


class IsolatedLinuxDevComputer:
    provider_name = "isolated_linux_dev"
    isolation_tier = "filesystem_sandbox"

    def __init__(self, record: ComputerRecord, settings: Settings, policy: NavigationPolicy):
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
        self._write_isolation_manifest()

    def _write_isolation_manifest(self) -> None:
        meta = self.paths["metadata"]
        meta.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "computer_id": self.record.id,
            "teammate_id": self.record.teammate_id,
            "note": ISOLATION_NOTE,
            "no_host_env_secrets": True,
            "no_docker_socket": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (meta / "isolation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (meta / "README.md").write_text(
            "# Isolated Linux-dev computer\n\n"
            + ISOLATION_NOTE
            + "\n\nWorkspace and artifacts persist under this root. "
            "Deleting the computer removes this directory after confirm=true.\n",
            encoding="utf-8",
        )

    async def provision(self) -> dict[str, Any]:
        self._write_isolation_manifest()
        return {
            "computer_id": self.record.id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "root": str(self.paths["root"]),
            "note": ISOLATION_NOTE,
        }

    async def start(self, *, headed: bool = False) -> dict[str, Any]:
        await self.open_browser(headed=headed)
        return {"state": "ready", "browser_open": True, "healthy": True}

    async def stop(self) -> dict[str, Any]:
        await self.close_browser()
        return {"state": "stopped", "browser_open": False}

    async def health(self) -> dict[str, Any]:
        root_ok = self.paths["root"].is_dir() and self.paths["workspace"].is_dir()
        browser_alive = self.browser.is_open
        return {
            "healthy": root_ok,
            "root_ok": root_ok,
            "browser_process_alive": browser_alive,
            "isolation_tier": self.isolation_tier,
            "note": "Filesystem presence checked; not a cloud hypervisor health probe.",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]:
        self.browser.headless = not headed
        await self.browser.ensure_session()
        return {"opened": True, "headless": self.browser.headless, "url": self.browser.last_url}

    async def close_browser(self) -> dict[str, Any]:
        return await self.browser.close()

    async def status(self) -> dict[str, Any]:
        h = await self.health()
        return {
            "computer_id": self.record.id,
            "teammate_id": self.record.teammate_id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "browser_open": self.browser.is_open,
            "url": self.browser.last_url,
            "health": h,
            "isolation_note": ISOLATION_NOTE,
        }

    async def preview(self) -> bytes:
        return await self.browser.preview_bytes(max_width=self.settings.computer_preview_max_width)

    async def enter_human_control(self) -> dict[str, Any]:
        self.browser.agent_blocked_reason = "Human control is active. Agent browser actions are paused."
        if self.browser.is_open and self.browser.headless:
            await self.browser.reopen(headless=False)
        elif not self.browser.is_open:
            await self.open_browser(headed=True)
        return {
            "human_control": True,
            "control_state": "human_control",
            "url": self.browser.refresh_url_from_page(),
            "note": "Same isolated profile — not everyday Chrome; not a cloud VNC endpoint.",
        }

    async def release_human_control(self) -> dict[str, Any]:
        url = self.browser.refresh_url_from_page()
        self.browser.agent_blocked_reason = None
        if self.browser.is_open and not self.browser.headless and self.settings.computer_headless_default:
            await self.browser.reopen(headless=True)
        return {
            "human_control": False,
            "control_state": "agent_control",
            "url": url,
            "note": "Browser state refreshed after human control. Prior page assumptions are invalid.",
        }

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise RuntimeError("Delete requires confirm=true — this removes workspace and profile data.")
        await self.close_browser()
        root = self.paths["root"]
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)
        return {"deleted": True, "computer_id": self.record.id, "root_removed": str(root)}

    def workspace_paths(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.paths.items()}
