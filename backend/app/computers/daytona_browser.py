"""BrowserService-compatible adapter for Daytona cloud_sandbox.

Maps browser_open / screenshot / close onto desktop computer_use.
CSS selector click/type are not available remotely — fail with a clear redirect
to desktop_click / desktop_type after desktop_screenshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..browser.policy import NavigationDenied
from ..browser.session import BrowserServiceError

if TYPE_CHECKING:
    from .cloud_sandbox import CloudSandboxComputer


class DaytonaBrowserAdapter:
    """Drop-in session for tool_handlers when provider is cloud_sandbox."""

    def __init__(self, computer: CloudSandboxComputer):
        self._computer = computer
        self.headless = True
        self.last_url: str | None = None
        self.last_error: str | None = None
        self.agent_blocked_reason: str | None = None
        self._actions = 0

    @property
    def is_open(self) -> bool:
        return bool(getattr(self._computer, "_computer_use_started", False))

    def _assert_agent_may_act(self) -> None:
        if self.agent_blocked_reason:
            raise BrowserServiceError(self.agent_blocked_reason)

    async def ensure_session(self) -> None:
        self._assert_agent_may_act()
        if not self.is_open:
            await self._computer.open_browser()

    async def reopen(self, *, headless: bool | None = None) -> None:
        if headless is not None:
            self.headless = headless
        await self._computer.open_browser()

    async def close(self) -> dict[str, Any]:
        result = await self._computer.close_browser()
        return {"closed": True, **result, "note": "Daytona computer_use desktop stopped."}

    async def open(self, url: str) -> dict[str, Any]:
        self._assert_agent_may_act()
        # Policy is enforced inside desktop_open_url via ComputerManager.policy when called
        # from tools; here we rely on CloudSandboxComputer.desktop_open_url after policy in tools.
        # Direct open() from adapter still goes through NavigationPolicy on the computer's manager
        # when invoked from browser tools — tool_handlers call session.open which is this method.
        # Apply policy via computer.settings path: use computer client's parent policy if available.
        from ..browser.policy import NavigationPolicy

        # Prefer the manager policy stored on computer when session_factory wiring exists;
        # fall back to a default fail-closed policy.
        policy = getattr(self._computer, "_nav_policy", None) or NavigationPolicy(fixture_mode=False)
        try:
            safe = policy.assert_allowed(url)
        except NavigationDenied as exc:
            raise BrowserServiceError(exc.reason) from exc
        out = await self._computer.desktop_open_url(url=safe)
        self.last_url = safe
        self._actions += 1
        return {
            "opened": True,
            "url": safe,
            "provider": "cloud_sandbox",
            "note": out.get("note"),
            "untrusted": False,
        }

    async def snapshot(self) -> dict[str, Any]:
        self._assert_agent_may_act()
        await self.ensure_session()
        # No DOM access; return honest limited evidence + encourage screenshot.
        return {
            "url": self.last_url or self._computer.browser.last_url,
            "title": None,
            "text": (
                "Daytona cloud_sandbox has no DOM snapshot. "
                "Call browser_screenshot or desktop_screenshot, then use desktop_click "
                "with coordinates (CSS selectors are not supported on this provider)."
            ),
            "provider": "cloud_sandbox",
            "untrusted": True,
            "note": "Text content is not extracted from the remote desktop.",
        }

    async def extract(self, selectors: dict[str, str] | None = None) -> dict[str, Any]:
        self._assert_agent_may_act()
        _ = selectors
        raise BrowserServiceError(
            "browser_extract (CSS selectors) is not available on cloud_sandbox. "
            "Use desktop_screenshot and desktop_click/desktop_type instead."
        )

    async def click(self, selector: str) -> dict[str, Any]:
        self._assert_agent_may_act()
        _ = selector
        raise BrowserServiceError(
            "browser_click CSS selectors are not available on Daytona cloud_sandbox. "
            "Use desktop_screenshot, then desktop_click with x/y coordinates."
        )

    async def type_text(self, selector: str, text: str) -> dict[str, Any]:
        self._assert_agent_may_act()
        _ = selector, text
        raise BrowserServiceError(
            "browser_type CSS selectors are not available on Daytona cloud_sandbox. "
            "Focus with desktop_click, then desktop_type."
        )

    async def screenshot(self, name: str = "page") -> dict[str, Any]:
        self._assert_agent_may_act()
        await self.ensure_session()
        data = await self._computer.preview()
        artifacts = Path(self._computer.paths["artifacts"])
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64] or "page"
        path = artifacts / f"{safe}.png"
        path.write_bytes(data)
        return {
            "path": str(path),
            "bytes": len(data),
            "url": self.last_url,
            "provider": "cloud_sandbox",
        }

    async def preview_bytes(self, *, max_width: int = 1024) -> bytes:
        _ = max_width
        return await self._computer.preview()

    def refresh_url_from_page(self) -> str | None:
        return self.last_url

    def current_host(self) -> str | None:
        from urllib.parse import urlparse

        url = self.refresh_url_from_page()
        if not url:
            return None
        return urlparse(url).hostname
