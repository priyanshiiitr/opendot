"""Google Cloud Compute Engine computer provider (credit-funded demo path)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..database import ComputerRecord
from .credit_policy import CreditPolicyError, assert_credit_funded_gcp_allowed, build_cost_estimate
from .gcp_client import GcpClientError, GcpComputeClient, build_gcp_client
from .paths import ensure_layout
from .workspace import WorkspaceStore

ISOLATION_NOTE = (
    "gcp_compute: real Google Compute Engine VM (credit-funded path). "
    "No public VNC. Guest agent pulls commands from the control plane. "
    "Not operational until credentials + credit-funded mode are configured and a VM lifecycle is verified."
)

# Minimal 1x1 PNG used when guest agent has not uploaded a preview yet is avoided —
# preview fails closed instead of faking a desktop.


def _instance_name(computer_id: str) -> str:
    # GCE names: lowercase, max 63, must start with letter.
    short = computer_id.replace("-", "")[:20]
    return f"aitm-{short}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


GUEST_STARTUP_SCRIPT = r'''#!/bin/bash
set -euo pipefail
# AI Teammates guest agent bootstrap — no inbound VNC/desktop ports.
# Pulls work from the control plane over HTTPS; does not expose shell to the public internet.
META_URL="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
HDR=(-H "Metadata-Flavor: Google")
CONTROL_PLANE=$(curl -sf "${HDR[@]}" "${META_URL}/ai-teammates-control-plane" || true)
AGENT_TOKEN=$(curl -sf "${HDR[@]}" "${META_URL}/ai-teammates-agent-token" || true)
COMPUTER_ID=$(curl -sf "${HDR[@]}" "${META_URL}/ai-teammates-computer-id" || true)
if [[ -z "${CONTROL_PLANE}" || -z "${AGENT_TOKEN}" || -z "${COMPUTER_ID}" ]]; then
  echo "Missing AI Teammates metadata; guest agent not started." >&2
  exit 0
fi
mkdir -p /opt/ai-teammates
cat >/opt/ai-teammates/guest_agent.py <<'PY'
import json, os, time, urllib.request
CONTROL = os.environ["CONTROL_PLANE"].rstrip("/")
TOKEN = os.environ["AGENT_TOKEN"]
CID = os.environ["COMPUTER_ID"]
HDR = {"X-Computer-Agent-Token": TOKEN, "Content-Type": "application/json"}

def post(path, body):
    req = urllib.request.Request(
        CONTROL + path, data=json.dumps(body).encode(), headers=HDR, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")

def get(path):
    req = urllib.request.Request(CONTROL + path, headers={"X-Computer-Agent-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")

while True:
    try:
        post(f"/api/internal/computers/{CID}/agent/heartbeat", {"status": "alive"})
        cmds = get(f"/api/internal/computers/{CID}/agent/commands")
        for cmd in cmds.get("commands") or []:
            if cmd.get("type") == "preview_request":
                # Placeholder PNG until Chromium desktop stack is installed on the image.
                png = (
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
                    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
                )
                import base64
                post(
                    f"/api/internal/computers/{CID}/agent/preview",
                    {"png_b64": base64.b64encode(png).decode(), "note": "guest-agent-placeholder"},
                )
    except Exception as exc:
        print("guest agent loop error:", exc, flush=True)
    time.sleep(5)
PY
export CONTROL_PLANE AGENT_TOKEN COMPUTER_ID
nohup python3 /opt/ai-teammates/guest_agent.py >/var/log/ai-teammates-agent.log 2>&1 &
'''


class GcpComputeComputer:
    provider_name = "gcp_compute"
    isolation_tier = "vm"

    def __init__(
        self,
        record: ComputerRecord,
        settings: Settings,
        client: GcpComputeClient | None = None,
        *,
        session_factory=None,
    ):
        self.record = record
        self.settings = settings
        self.client = client or build_gcp_client()
        self.session_factory = session_factory
        root = Path(record.root_path)
        self.paths = {
            "root": root,
            "profile": root / "profile",  # unused locally; kept for layout compatibility
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
        # Browser tools on GCP go through the guest agent — no local Playwright process.
        self.browser = _GcpBrowserStub(self)
        self._action_lock = asyncio.Lock()
        self._pending_commands: list[dict[str, Any]] = []
        self._agent_token_plain: str | None = None
        self._last_heartbeat: str | None = None

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
                    row.external_ref = meta.get("instance_name")
                    db.commit()

    def estimate(self) -> dict[str, Any]:
        return build_cost_estimate(self.settings).as_dict()

    async def provision(self, *, confirm_estimate: bool = False) -> dict[str, Any]:
        if self.session_factory is None:
            raise RuntimeError("session_factory required for gcp provision policy checks")
        with self.session_factory() as db:
            try:
                estimate = assert_credit_funded_gcp_allowed(
                    self.settings, db, exclude_computer_id=self.record.id
                )
            except CreditPolicyError as exc:
                raise GcpClientError(exc.reason) from exc
        estimate_dict = estimate.as_dict()
        if not confirm_estimate:
            return {
                "requires_confirmation": True,
                "estimated_cost": estimate_dict,
                "computer_id": self.record.id,
                "provider": self.provider_name,
                "message": (
                    "Review the estimated cost, then re-submit provision with "
                    "confirm_estimate=true to create at most one credit-funded VM."
                ),
                "created": False,
            }
        if not self.client.credentials_ready():
            raise GcpClientError(
                "GCP credentials are not ready. Configure Application Default Credentials "
                "or AI_TEAMMATES_GCP_CREDENTIALS_FILE (path only — never paste secrets in chat)."
            )
        meta = self._meta()
        if meta.get("instance_name") and meta.get("provisioned"):
            # Idempotent: do not create a second VM
            return {
                "computer_id": self.record.id,
                "provider": self.provider_name,
                "idempotent": True,
                "instance_name": meta["instance_name"],
                "estimated_cost": estimate_dict,
                "created": False,
            }
        token = secrets.token_urlsafe(32)
        self._agent_token_plain = token
        instance_name = _instance_name(self.record.id)
        control = (self.settings.gcp_control_plane_url or "").rstrip("/")
        if not control:
            raise GcpClientError(
                "AI_TEAMMATES_GCP_CONTROL_PLANE_URL is required so the guest agent can "
                "reach the always-on control plane (no public VNC)."
            )
        labels = {
            "ai-teammates": "true",
            "computer-id": self.record.id[:63].lower().replace("_", "-"),
            "teammate-id": str(self.record.teammate_id),
        }
        metadata = {
            "ai-teammates-computer-id": self.record.id,
            "ai-teammates-agent-token": token,
            "ai-teammates-control-plane": control,
        }
        try:
            inst = await asyncio.to_thread(
                self.client.create_instance,
                project=self.settings.gcp_project_id.strip(),
                zone=self.settings.gcp_zone.strip(),
                name=instance_name,
                machine_type=self.settings.gcp_machine_type.strip(),
                disk_gb=self.settings.gcp_boot_disk_gb,
                labels=labels,
                metadata=metadata,
                startup_script=GUEST_STARTUP_SCRIPT,
            )
        except GcpClientError:
            raise
        except Exception as exc:
            raise GcpClientError(f"GCP create_instance failed: {exc}") from exc
        meta.update(
            {
                "provisioned": True,
                "instance_name": inst.name,
                "zone": inst.zone,
                "machine_type": inst.machine_type,
                "self_link": inst.self_link,
                "agent_token_sha256": _hash_token(token),
                "estimated_cost": estimate_dict,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "idle_stop_minutes": self.settings.computer_idle_stop_minutes,
            }
        )
        self._save_meta(meta)
        (self.paths["metadata"] / "gcp.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {
            "computer_id": self.record.id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "instance_name": inst.name,
            "status": inst.status,
            "estimated_cost": estimate_dict,
            "created": True,
            "agent_token_issued": True,
            "note": ISOLATION_NOTE,
            "security": "No public VNC; guest agent uses pull model to control plane.",
        }

    async def start(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed
        meta = self._meta()
        name = meta.get("instance_name")
        if not name:
            raise GcpClientError("Instance not provisioned.")
        inst = await asyncio.to_thread(
            self.client.start_instance,
            project=self.settings.gcp_project_id.strip(),
            zone=meta.get("zone") or self.settings.gcp_zone.strip(),
            name=name,
        )
        return {"state": "ready" if inst.status == "RUNNING" else inst.status.lower(), "status": inst.status}

    async def stop(self) -> dict[str, Any]:
        meta = self._meta()
        name = meta.get("instance_name")
        if not name:
            return {"state": "stopped"}
        inst = await asyncio.to_thread(
            self.client.stop_instance,
            project=self.settings.gcp_project_id.strip(),
            zone=meta.get("zone") or self.settings.gcp_zone.strip(),
            name=name,
        )
        return {"state": "stopped", "status": inst.status}

    async def health(self) -> dict[str, Any]:
        meta = self._meta()
        name = meta.get("instance_name")
        if not name:
            return {
                "healthy": False,
                "configured": bool(self.settings.gcp_project_id),
                "note": "No instance provisioned yet.",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        try:
            inst = await asyncio.to_thread(
                self.client.get_instance,
                project=self.settings.gcp_project_id.strip(),
                zone=meta.get("zone") or self.settings.gcp_zone.strip(),
                name=name,
            )
            healthy = inst.status == "RUNNING"
            return {
                "healthy": healthy,
                "status": inst.status,
                "instance_name": inst.name,
                "machine_type": inst.machine_type,
                "agent_heartbeat": self._last_heartbeat,
                "isolation_tier": self.isolation_tier,
                "note": "Compute Engine API status; DB ready ≠ guest agent ready.",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except GcpClientError as exc:
            return {"healthy": False, "error": exc.reason, "checked_at": datetime.now(timezone.utc).isoformat()}

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed
        await self.start()
        self.browser.is_open = True
        self._pending_commands.append({"type": "open_browser", "at": datetime.now(timezone.utc).isoformat()})
        return {"opened": True, "via": "guest_agent_command_queue"}

    async def close_browser(self) -> dict[str, Any]:
        self.browser.is_open = False
        self._pending_commands.append({"type": "close_browser", "at": datetime.now(timezone.utc).isoformat()})
        return {"closed": True}

    async def status(self) -> dict[str, Any]:
        h = await self.health()
        return {
            "computer_id": self.record.id,
            "teammate_id": self.record.teammate_id,
            "provider": self.provider_name,
            "isolation_tier": self.isolation_tier,
            "health": h,
            "isolation_note": ISOLATION_NOTE,
            "meta": {k: v for k, v in self._meta().items() if k != "agent_token_sha256"},
        }

    async def preview(self) -> bytes:
        path = self.paths["preview"]
        if path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()
        self._pending_commands.append({"type": "preview_request", "at": datetime.now(timezone.utc).isoformat()})
        raise GcpClientError(
            "No guest-agent preview yet. Start the VM and wait for the agent heartbeat, "
            "or the guest agent has not uploaded a screenshot."
        )

    async def enter_human_control(self) -> dict[str, Any]:
        self._pending_commands.append({"type": "human_control", "at": datetime.now(timezone.utc).isoformat()})
        self.browser.agent_blocked_reason = "Human control is active. Agent browser actions are paused."
        return {
            "human_control": True,
            "control_state": "human_control",
            "note": "Agent actions paused. Desktop interaction goes through authenticated app UI / guest agent — not public VNC.",
        }

    async def release_human_control(self) -> dict[str, Any]:
        self._pending_commands.append({"type": "release_control", "at": datetime.now(timezone.utc).isoformat()})
        self.browser.agent_blocked_reason = None
        return {
            "human_control": False,
            "control_state": "agent_control",
            "note": "State must be refreshed from the guest agent after human control.",
        }

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise GcpClientError("Delete requires confirm=true — this deletes the GCP VM and local workspace mirror.")
        meta = self._meta()
        name = meta.get("instance_name")
        if name:
            try:
                await asyncio.to_thread(
                    self.client.delete_instance,
                    project=self.settings.gcp_project_id.strip(),
                    zone=meta.get("zone") or self.settings.gcp_zone.strip(),
                    name=name,
                )
            except GcpClientError as exc:
                if "not found" not in exc.reason.lower():
                    raise
        import shutil

        if self.paths["root"].is_dir():
            shutil.rmtree(self.paths["root"], ignore_errors=True)
        return {"deleted": True, "computer_id": self.record.id, "instance_name": name}

    def workspace_paths(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.paths.items()}

    def verify_agent_token(self, token: str) -> bool:
        meta = self._meta()
        expected = meta.get("agent_token_sha256")
        if not expected:
            return False
        return secrets.compare_digest(_hash_token(token), expected)

    def drain_commands(self) -> list[dict[str, Any]]:
        cmds = list(self._pending_commands)
        self._pending_commands.clear()
        return cmds

    def record_heartbeat(self) -> None:
        self._last_heartbeat = datetime.now(timezone.utc).isoformat()

    def store_preview_bytes(self, data: bytes) -> None:
        self.paths["preview"].write_bytes(data)


class _GcpBrowserStub:
    """Stand-in so ComputerManager can treat GCP like other providers for takeover flags."""

    def __init__(self, computer: GcpComputeComputer):
        self._computer = computer
        self.is_open = False
        self.headless = True
        self.last_url: str | None = None
        self.agent_blocked_reason: str | None = None

    def refresh_url_from_page(self) -> str | None:
        return self.last_url

    async def ensure_session(self) -> None:
        raise GcpClientError(
            "Browser tools on gcp_compute run inside the guest VM via the agent queue; "
            "local Playwright is not used."
        )

    async def open(self, url: str) -> dict[str, Any]:
        if self.agent_blocked_reason:
            from ..browser.session import BrowserServiceError

            raise BrowserServiceError(self.agent_blocked_reason)
        self._computer._pending_commands.append({"type": "navigate", "url": url})
        self.last_url = url
        self.is_open = True
        return {"ok": True, "queued": True, "url": url}

    async def close(self) -> dict[str, Any]:
        self.is_open = False
        return {"closed": True}

    async def reopen(self, *, headless: bool = True) -> None:
        self.headless = headless

    async def preview_bytes(self, max_width: int = 1024) -> bytes:
        _ = max_width
        return await self._computer.preview()
