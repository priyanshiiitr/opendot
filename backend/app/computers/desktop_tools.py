"""Daytona desktop tools (cloud_sandbox only) — coordinate click/type + screenshot.

Playwright browser_* tools remain the local_browser path. These tools refuse on
other providers so the model cannot silently drive the wrong computer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..browser.policy import NavigationDenied
from ..tools import ToolError, ToolRegistry, ToolSpec
from .cloud_sandbox import CloudSandboxComputer, CloudSandboxNotConfigured
from .context import current_teammate_id
from .daytona_client import DaytonaClientError
from .manager import ComputerError, ComputerManager


class DesktopScreenshotArguments(BaseModel):
    """Capture the Daytona desktop (PNG via computer_use)."""


class DesktopOpenUrlArguments(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class DesktopClickArguments(BaseModel):
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False


class DesktopTypeArguments(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DesktopHotkeyArguments(BaseModel):
    keys: str = Field(min_length=1, max_length=80)

    @field_validator("keys")
    @classmethod
    def sanitize_keys(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789+-_")
        if not cleaned or any(c not in allowed for c in cleaned):
            raise ValueError("hotkey must look like ctrl+c or alt+tab")
        return cleaned


async def _cloud_computer(computers: ComputerManager) -> CloudSandboxComputer:
    tid = current_teammate_id.get()
    if tid is None:
        raise ToolError("No teammate computer context for desktop tools.")
    with computers.session_factory() as db:
        record = computers.get_or_create_record(db, tid)
        if record.provider != "cloud_sandbox":
            raise ToolError(
                f"desktop_* tools require provider cloud_sandbox (this teammate uses '{record.provider}'). "
                "Use browser_* tools with local_browser, or provision cloud_sandbox after delete+confirm."
            )
        live = await computers._live_for(record)
    if not isinstance(live, CloudSandboxComputer):
        raise ToolError("Desktop tools require a cloud_sandbox computer.")
    if live.browser.agent_blocked_reason:
        raise ToolError(live.browser.agent_blocked_reason)
    return live


def register_desktop_tools(registry: ToolRegistry, computers: ComputerManager, timeout_seconds: float) -> None:
    async def desktop_screenshot(_arguments: DesktopScreenshotArguments) -> dict[str, Any]:
        try:
            live = await _cloud_computer(computers)
            data = await live.preview()
            # Persist under artifacts for UI download parity with browser screenshots.
            path = live.paths["preview"]
            return {
                "action": "desktop_screenshot",
                "bytes": len(data),
                "path": str(path),
                "note": "PNG written to computer preview path; fetch via computer preview API.",
            }
        except (CloudSandboxNotConfigured, DaytonaClientError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def desktop_open_url(arguments: DesktopOpenUrlArguments) -> dict[str, Any]:
        try:
            safe = computers.policy.assert_allowed(arguments.url)
            live = await _cloud_computer(computers)
            return await live.desktop_open_url(url=safe)
        except NavigationDenied as exc:
            raise ToolError(exc.reason) from exc
        except (CloudSandboxNotConfigured, DaytonaClientError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def desktop_click(arguments: DesktopClickArguments) -> dict[str, Any]:
        try:
            live = await _cloud_computer(computers)
            return await live.desktop_click(
                x=arguments.x, y=arguments.y, button=arguments.button, double=arguments.double
            )
        except (CloudSandboxNotConfigured, DaytonaClientError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def desktop_type(arguments: DesktopTypeArguments) -> dict[str, Any]:
        try:
            live = await _cloud_computer(computers)
            return await live.desktop_type(text=arguments.text)
        except (CloudSandboxNotConfigured, DaytonaClientError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def desktop_hotkey(arguments: DesktopHotkeyArguments) -> dict[str, Any]:
        try:
            live = await _cloud_computer(computers)
            return await live.desktop_hotkey(keys=arguments.keys)
        except (CloudSandboxNotConfigured, DaytonaClientError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    registry.register(
        ToolSpec(
            "desktop_screenshot",
            "Capture a PNG screenshot of the teammate's Daytona cloud_sandbox desktop.",
            DesktopScreenshotArguments,
            "read_only",
            timeout_seconds,
            desktop_screenshot,
        )
    )
    registry.register(
        ToolSpec(
            "desktop_open_url",
            "Open an http(s) URL in Chromium inside the Daytona cloud_sandbox (navigation policy enforced).",
            DesktopOpenUrlArguments,
            "read_only",
            timeout_seconds,
            desktop_open_url,
        )
    )
    registry.register(
        ToolSpec(
            "desktop_click",
            "Click at desktop coordinates inside the Daytona cloud_sandbox (requires approval).",
            DesktopClickArguments,
            "approval_required",
            timeout_seconds,
            desktop_click,
        )
    )
    registry.register(
        ToolSpec(
            "desktop_type",
            "Type text into the focused Daytona cloud_sandbox desktop window (requires approval).",
            DesktopTypeArguments,
            "approval_required",
            timeout_seconds,
            desktop_type,
        )
    )
    registry.register(
        ToolSpec(
            "desktop_hotkey",
            "Press a hotkey combo on the Daytona cloud_sandbox desktop, e.g. ctrl+c (requires approval).",
            DesktopHotkeyArguments,
            "approval_required",
            timeout_seconds,
            desktop_hotkey,
        )
    )
