"""Daytona sandbox client — real SDK or in-process fake for tests.

Never logs API keys. Real client is only constructed when credentials exist.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class DaytonaClientError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class DaytonaSandboxInfo:
    id: str
    name: str | None
    state: str  # started | stopped | creating | error | destroyed | ...
    labels: dict[str, str] = field(default_factory=dict)
    public: bool = False


@runtime_checkable
class DaytonaSandboxClient(Protocol):
    def credentials_ready(self) -> bool: ...

    async def create_sandbox(
        self,
        *,
        name: str,
        labels: dict[str, str],
        auto_stop_minutes: int,
        public: bool = False,
    ) -> DaytonaSandboxInfo: ...

    async def get_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo: ...

    async def start_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo: ...

    async def stop_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo: ...

    async def delete_sandbox(self, sandbox_id: str) -> None: ...

    async def exec_command(self, sandbox_id: str, command: str) -> dict[str, Any]: ...

    async def start_computer_use(self, sandbox_id: str) -> dict[str, Any]: ...

    async def stop_computer_use(self, sandbox_id: str) -> dict[str, Any]: ...

    async def screenshot_png(self, sandbox_id: str) -> bytes: ...

    async def mouse_click(
        self, sandbox_id: str, *, x: int, y: int, button: str = "left", double: bool = False
    ) -> dict[str, Any]: ...

    async def keyboard_type(self, sandbox_id: str, *, text: str) -> dict[str, Any]: ...

    async def keyboard_hotkey(self, sandbox_id: str, *, keys: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...


# Minimal valid 1x1 PNG for FakeDaytonaClient / contract tests.
_FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeDaytonaClient:
    """Deterministic in-memory Daytona for contract tests — never talks to the network."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sandboxes: dict[str, DaytonaSandboxInfo] = {}
        self._computer_use: dict[str, bool] = {}
        self.create_calls = 0
        self.delete_calls = 0
        self.screenshot_calls = 0
        self.computer_use_starts = 0
        self.click_calls = 0
        self.type_calls = 0
        self.hotkey_calls = 0
        self.exec_commands: list[str] = []
        self.fail_create: str | None = None
        self.credentials_ok = True

    def credentials_ready(self) -> bool:
        return self.credentials_ok

    async def create_sandbox(
        self,
        *,
        name: str,
        labels: dict[str, str],
        auto_stop_minutes: int,
        public: bool = False,
    ) -> DaytonaSandboxInfo:
        _ = auto_stop_minutes
        with self._lock:
            self.create_calls += 1
            if self.fail_create:
                raise DaytonaClientError(self.fail_create)
            sid = f"fake-{uuid.uuid4().hex[:12]}"
            info = DaytonaSandboxInfo(
                id=sid,
                name=name,
                state="started",
                labels=dict(labels),
                public=public,
            )
            self._sandboxes[sid] = info
            return info

    async def get_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo:
        with self._lock:
            info = self._sandboxes.get(sandbox_id)
            if not info or info.state == "destroyed":
                raise DaytonaClientError(f"Sandbox not found: {sandbox_id}")
            return DaytonaSandboxInfo(
                id=info.id,
                name=info.name,
                state=info.state,
                labels=dict(info.labels),
                public=info.public,
            )

    async def start_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo:
        with self._lock:
            info = self._sandboxes.get(sandbox_id)
            if not info:
                raise DaytonaClientError(f"Sandbox not found: {sandbox_id}")
            info.state = "started"
            return DaytonaSandboxInfo(
                id=info.id, name=info.name, state=info.state, labels=dict(info.labels), public=info.public
            )

    async def stop_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo:
        with self._lock:
            info = self._sandboxes.get(sandbox_id)
            if not info:
                raise DaytonaClientError(f"Sandbox not found: {sandbox_id}")
            info.state = "stopped"
            return DaytonaSandboxInfo(
                id=info.id, name=info.name, state=info.state, labels=dict(info.labels), public=info.public
            )

    async def delete_sandbox(self, sandbox_id: str) -> None:
        with self._lock:
            self.delete_calls += 1
            info = self._sandboxes.get(sandbox_id)
            if info:
                info.state = "destroyed"

    async def exec_command(self, sandbox_id: str, command: str) -> dict[str, Any]:
        await self.get_sandbox(sandbox_id)
        with self._lock:
            self.exec_commands.append(command)
        return {"exit_code": 0, "result": f"fake-ok:{command[:80]}"}

    async def start_computer_use(self, sandbox_id: str) -> dict[str, Any]:
        await self.get_sandbox(sandbox_id)
        with self._lock:
            self.computer_use_starts += 1
            self._computer_use[sandbox_id] = True
        return {"started": True, "message": "fake computer_use started"}

    async def stop_computer_use(self, sandbox_id: str) -> dict[str, Any]:
        with self._lock:
            self._computer_use[sandbox_id] = False
        return {"stopped": True}

    async def screenshot_png(self, sandbox_id: str) -> bytes:
        await self.get_sandbox(sandbox_id)
        with self._lock:
            self.screenshot_calls += 1
            if not self._computer_use.get(sandbox_id):
                raise DaytonaClientError("computer_use not started")
        return _FAKE_PNG

    async def mouse_click(
        self, sandbox_id: str, *, x: int, y: int, button: str = "left", double: bool = False
    ) -> dict[str, Any]:
        await self.get_sandbox(sandbox_id)
        with self._lock:
            if not self._computer_use.get(sandbox_id):
                raise DaytonaClientError("computer_use not started")
            self.click_calls += 1
        return {"x": x, "y": y, "button": button, "double": double}

    async def keyboard_type(self, sandbox_id: str, *, text: str) -> dict[str, Any]:
        await self.get_sandbox(sandbox_id)
        with self._lock:
            if not self._computer_use.get(sandbox_id):
                raise DaytonaClientError("computer_use not started")
            self.type_calls += 1
        return {"typed_chars": len(text)}

    async def keyboard_hotkey(self, sandbox_id: str, *, keys: str) -> dict[str, Any]:
        await self.get_sandbox(sandbox_id)
        with self._lock:
            if not self._computer_use.get(sandbox_id):
                raise DaytonaClientError("computer_use not started")
            self.hotkey_calls += 1
        return {"keys": keys}

    async def close(self) -> None:
        return None


class SdkDaytonaClient:
    """Thin async wrapper around the official daytona SDK."""

    def __init__(self, *, api_key: str, api_url: str = "", target: str = ""):
        if not api_key.strip():
            raise DaytonaClientError("Daytona API key is empty.")
        self._api_key = api_key.strip()
        self._api_url = (api_url or "").strip() or None
        self._target = (target or "").strip() or None
        self._daytona = None

    def credentials_ready(self) -> bool:
        return bool(self._api_key)

    async def _client(self):
        if self._daytona is not None:
            return self._daytona
        try:
            from daytona import AsyncDaytona, DaytonaConfig
        except ImportError as exc:
            raise DaytonaClientError(
                "daytona package is not installed. Run: pip install daytona"
            ) from exc
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._api_url:
            kwargs["api_url"] = self._api_url
        if self._target:
            kwargs["target"] = self._target
        self._daytona = AsyncDaytona(DaytonaConfig(**kwargs))
        return self._daytona

    @staticmethod
    def _info_from(sandbox: Any) -> DaytonaSandboxInfo:
        state = getattr(sandbox, "state", None)
        state_val = getattr(state, "value", None) or str(state or "unknown")
        labels = dict(getattr(sandbox, "labels", None) or {})
        return DaytonaSandboxInfo(
            id=str(getattr(sandbox, "id")),
            name=getattr(sandbox, "name", None),
            state=str(state_val),
            labels=labels,
            public=bool(getattr(sandbox, "public", False)),
        )

    async def create_sandbox(
        self,
        *,
        name: str,
        labels: dict[str, str],
        auto_stop_minutes: int,
        public: bool = False,
    ) -> DaytonaSandboxInfo:
        from daytona import CreateSandboxFromSnapshotParams

        client = await self._client()
        params = CreateSandboxFromSnapshotParams(
            name=name[:60],
            labels=labels,
            public=public,
            auto_stop_interval=max(1, int(auto_stop_minutes)),
            network_block_all=False,
        )
        try:
            sandbox = await client.create(params, timeout=120)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona create failed: {exc}") from exc
        return self._info_from(sandbox)

    async def get_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona get failed: {exc}") from exc
        return self._info_from(sandbox)

    async def start_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            await client.start(sandbox)
            sandbox = await client.get(sandbox_id)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona start failed: {exc}") from exc
        return self._info_from(sandbox)

    async def stop_sandbox(self, sandbox_id: str) -> DaytonaSandboxInfo:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            await client.stop(sandbox)
            sandbox = await client.get(sandbox_id)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona stop failed: {exc}") from exc
        return self._info_from(sandbox)

    async def delete_sandbox(self, sandbox_id: str) -> None:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            await client.delete(sandbox, wait=True)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona delete failed: {exc}") from exc

    async def exec_command(self, sandbox_id: str, command: str) -> dict[str, Any]:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            response = await sandbox.process.exec(command)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona exec failed: {exc}") from exc
        return {
            "exit_code": getattr(response, "exit_code", None),
            "result": getattr(response, "result", str(response)),
        }

    async def start_computer_use(self, sandbox_id: str) -> dict[str, Any]:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            result = await sandbox.computer_use.start()
        except Exception as exc:
            raise DaytonaClientError(f"Daytona computer_use start failed: {exc}") from exc
        return {"started": True, "message": getattr(result, "message", str(result))}

    async def stop_computer_use(self, sandbox_id: str) -> dict[str, Any]:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            result = await sandbox.computer_use.stop()
        except Exception as exc:
            raise DaytonaClientError(f"Daytona computer_use stop failed: {exc}") from exc
        return {"stopped": True, "message": getattr(result, "message", str(result))}

    async def screenshot_png(self, sandbox_id: str) -> bytes:
        import base64

        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            shot = await sandbox.computer_use.screenshot.take_full_screen()
        except Exception as exc:
            raise DaytonaClientError(f"Daytona screenshot failed: {exc}") from exc
        raw = getattr(shot, "screenshot", None)
        if raw is None:
            raise DaytonaClientError("Daytona screenshot response missing image data.")
        if isinstance(raw, (bytes, bytearray)):
            data = bytes(raw)
        else:
            text = str(raw)
            if "," in text and text.strip().startswith("data:"):
                text = text.split(",", 1)[1]
            try:
                data = base64.b64decode(text)
            except Exception as exc:
                raise DaytonaClientError(f"Daytona screenshot decode failed: {exc}") from exc
        if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
            # Some responses may already be raw PNG; otherwise reject non-images.
            if not data.startswith(b"\x89PNG"):
                raise DaytonaClientError("Daytona screenshot did not return PNG bytes.")
        return data

    async def mouse_click(
        self, sandbox_id: str, *, x: int, y: int, button: str = "left", double: bool = False
    ) -> dict[str, Any]:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            result = await sandbox.computer_use.mouse.click(x, y, button=button, double=double)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona mouse click failed: {exc}") from exc
        return {
            "x": getattr(result, "x", x),
            "y": getattr(result, "y", y),
            "button": button,
            "double": double,
        }

    async def keyboard_type(self, sandbox_id: str, *, text: str) -> dict[str, Any]:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            await sandbox.computer_use.keyboard.type(text)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona keyboard type failed: {exc}") from exc
        return {"typed_chars": len(text)}

    async def keyboard_hotkey(self, sandbox_id: str, *, keys: str) -> dict[str, Any]:
        client = await self._client()
        try:
            sandbox = await client.get(sandbox_id)
            await sandbox.computer_use.keyboard.hotkey(keys)
        except Exception as exc:
            raise DaytonaClientError(f"Daytona hotkey failed: {exc}") from exc
        return {"keys": keys}

    async def close(self) -> None:
        if self._daytona is not None:
            try:
                await self._daytona.close()
            except Exception:
                pass
            self._daytona = None


def resolve_daytona_api_key(settings_key: str | None = None) -> str:
    """Prefer settings field, then DAYTONA_API_KEY / AI_TEAMMATES_DAYTONA_API_KEY env."""
    import os

    for candidate in (
        (settings_key or "").strip(),
        (os.environ.get("DAYTONA_API_KEY") or "").strip(),
        (os.environ.get("AI_TEAMMATES_DAYTONA_API_KEY") or "").strip(),
    ):
        if candidate:
            return candidate
    return ""


def build_daytona_client(
    *,
    api_key: str | None = None,
    api_url: str = "",
    target: str = "",
) -> DaytonaSandboxClient:
    key = resolve_daytona_api_key(api_key)
    if not key:
        # Return a credentials-not-ready fake so callers can probe credentials_ready().
        fake = FakeDaytonaClient()
        fake.credentials_ok = False
        return fake
    return SdkDaytonaClient(api_key=key, api_url=api_url, target=target)
