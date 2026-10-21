"""Contract-test mock cloud provider — never presents as a real cloud computer."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import WorkspaceStore


@dataclass
class MockCloudComputer:
    provider_name: str = "mock_cloud"
    isolation_tier: str = "mock"
    computer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    teammate_id: int = 0
    state: str = "stopped"
    control_state: str = "agent_control"
    provisioned: bool = False
    deleted: bool = False
    browser_open: bool = False
    human_control: bool = False
    root: Path | None = None
    _workspace: WorkspaceStore | None = None
    provision_count: int = 0

    def _ensure_root(self) -> Path:
        if self.root is None:
            raise RuntimeError("root not set")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "workspace").mkdir(exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        if self._workspace is None:
            self._workspace = WorkspaceStore(self.root / "workspace", self.root / "artifacts")
        return self.root

    async def provision(self) -> dict[str, Any]:
        if self.deleted:
            raise RuntimeError("Cannot provision a deleted computer.")
        self.provision_count += 1
        if self.provisioned:
            # Idempotent: do not create a second resource
            return {"computer_id": self.computer_id, "state": self.state, "idempotent": True}
        self._ensure_root()
        self.provisioned = True
        self.state = "stopped"
        return {"computer_id": self.computer_id, "state": self.state, "isolation_tier": self.isolation_tier}

    async def start(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed
        if not self.provisioned or self.deleted:
            raise RuntimeError("Not provisioned.")
        self.state = "ready"
        return {"state": self.state, "healthy": True}

    async def stop(self) -> dict[str, Any]:
        self.browser_open = False
        self.state = "stopped"
        return {"state": self.state}

    async def health(self) -> dict[str, Any]:
        return {
            "healthy": self.provisioned and not self.deleted and self.state not in {"failed", "deleted"},
            "state": self.state,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "note": "mock health — not a real VM",
        }

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed
        if self.state not in {"ready", "busy", "human_control"}:
            await self.start()
        self.browser_open = True
        self.state = "busy"
        return {"opened": True, "url": "about:blank"}

    async def close_browser(self) -> dict[str, Any]:
        self.browser_open = False
        if self.state == "busy":
            self.state = "ready"
        return {"closed": True}

    async def status(self) -> dict[str, Any]:
        return {
            "computer_id": self.computer_id,
            "teammate_id": self.teammate_id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "state": self.state,
            "control_state": self.control_state,
            "browser_open": self.browser_open,
            "human_control": self.human_control,
            "provision_count": self.provision_count,
            "deleted": self.deleted,
        }

    async def preview(self) -> bytes:
        # Minimal valid PNG (1x1)
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
            b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    async def enter_human_control(self) -> dict[str, Any]:
        self.human_control = True
        self.control_state = "human_control"
        self.state = "human_control"
        return {"human_control": True, "control_state": self.control_state}

    async def release_human_control(self) -> dict[str, Any]:
        self.human_control = False
        self.control_state = "returning_to_agent"
        self.state = "ready"
        self.control_state = "agent_control"
        return {"human_control": False, "control_state": self.control_state, "note": "State refreshed after human control."}

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise RuntimeError("Delete requires confirm=true.")
        self.deleted = True
        self.provisioned = False
        self.state = "deleted"
        self.browser_open = False
        if self.root and self.root.exists():
            # Keep workspace policy: mock deletes metadata marker only in tests that opt in
            marker = self.root / "DELETED"
            marker.write_text("deleted", encoding="utf-8")
        return {"deleted": True, "computer_id": self.computer_id}

    def workspace_paths(self) -> dict[str, str]:
        root = self._ensure_root()
        return {"root": str(root), "workspace": str(root / "workspace"), "artifacts": str(root / "artifacts")}
