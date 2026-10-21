"""Credential storage — OS keyring in production, in-memory for tests."""

from __future__ import annotations

import logging
import sys
from typing import Protocol

logger = logging.getLogger(__name__)

SERVICE_NAME = "ai-teammates"


class CredentialStore(Protocol):
    writable: bool

    def set_secret(self, key: str, value: str) -> None: ...

    def get_secret(self, key: str) -> str | None: ...

    def delete_secret(self, key: str) -> None: ...

    def backend_name(self) -> str: ...


class InMemoryCredentialStore:
    """Test double only. Never use for real bot tokens outside automated tests."""

    writable = True

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def set_secret(self, key: str, value: str) -> None:
        self._data[key] = value

    def get_secret(self, key: str) -> str | None:
        return self._data.get(key)

    def delete_secret(self, key: str) -> None:
        self._data.pop(key, None)

    def backend_name(self) -> str:
        return "in_memory"


class DisabledCredentialStore:
    """Saving refused when no secure backend is available."""

    writable = False

    def set_secret(self, key: str, value: str) -> None:
        raise RuntimeError(
            "Secure credential storage is unavailable. On Windows install keyring and ensure "
            "Windows Credential Manager (WinVaultKeyring) is active. Plaintext storage is disabled."
        )

    def get_secret(self, key: str) -> str | None:
        return None

    def delete_secret(self, key: str) -> None:
        return None

    def backend_name(self) -> str:
        return "disabled"


class KeyringCredentialStore:
    def __init__(self, backend_name: str):
        import keyring

        self._keyring = keyring
        self._backend_name = backend_name
        self.writable = True

    def set_secret(self, key: str, value: str) -> None:
        self._keyring.set_password(SERVICE_NAME, key, value)

    def get_secret(self, key: str) -> str | None:
        return self._keyring.get_password(SERVICE_NAME, key)

    def delete_secret(self, key: str) -> None:
        try:
            self._keyring.delete_password(SERVICE_NAME, key)
        except Exception:
            return None

    def backend_name(self) -> str:
        return self._backend_name


def build_credential_store() -> CredentialStore:
    """Prefer Windows Credential Manager. Never silently fall back to plaintext."""
    try:
        import keyring
    except ImportError:
        logger.warning("keyring is not installed; credential saving disabled")
        return DisabledCredentialStore()

    if sys.platform == "win32":
        try:
            from keyring.backends.Windows import WinVaultKeyring

            keyring.set_keyring(WinVaultKeyring())
        except Exception as exc:
            logger.warning("Could not activate WinVaultKeyring: %s", exc.__class__.__name__)
            return DisabledCredentialStore()

    backend = keyring.get_keyring()
    name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
    lowered = name.lower()
    if "plaintext" in lowered or "fail" in lowered or "nullkeyring" in lowered:
        logger.warning("Refusing insecure keyring backend: %s", name)
        return DisabledCredentialStore()
    if sys.platform == "win32" and "winvault" not in lowered and "windows" not in lowered:
        logger.warning("Unexpected Windows keyring backend %s; refusing credential saves", name)
        return DisabledCredentialStore()
    return KeyringCredentialStore(name)
