"""Fail-closed destination policy for every browser navigation."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# Hostnames that must never be contacted even before DNS (defense in depth).
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)

ALLOWED_SCHEMES = frozenset({"http", "https"})


class NavigationDenied(Exception):
    """Structured refusal; safe to return to the model and UI."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


Resolver = Callable[..., Sequence[Any]]


@dataclass(frozen=True)
class NavigationPolicy:
    """Validate URL scheme, DNS-resolved IPs, and redirect targets.

    `fixture_mode` is a test-only escape hatch for controlled local HTML fixtures.
    It must stay false in normal application configuration.
    """

    fixture_mode: bool = False
    fixture_host: str = "127.0.0.1"
    fixture_port: int = 0
    resolver: Resolver = socket.getaddrinfo

    def assert_allowed(self, raw_url: str) -> str:
        if not raw_url or not isinstance(raw_url, str):
            raise NavigationDenied("A non-empty http(s) URL is required.")
        url = raw_url.strip()
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ALLOWED_SCHEMES:
            raise NavigationDenied(
                f"URL scheme '{scheme or '(none)'}' is not allowed. Only http and https are permitted."
            )
        if not parsed.hostname:
            raise NavigationDenied("URL is missing a hostname.")
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port or (443 if scheme == "https" else 80)
        if host in BLOCKED_HOSTNAMES and not self._fixture_exception(host, port):
            raise NavigationDenied(f"Host '{host}' is blocked by the navigation policy.")
        if self._fixture_exception(host, port):
            return url
        self._assert_public_addresses(host, port)
        return url

    def _fixture_exception(self, host: str, port: int) -> bool:
        if not self.fixture_mode or self.fixture_port <= 0:
            return False
        return host == self.fixture_host.lower() and port == self.fixture_port

    def _assert_public_addresses(self, host: str, port: int) -> None:
        try:
            # Prefer treating literal IPs without DNS.
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            addresses = self._resolve(host, port)
        if not addresses:
            raise NavigationDenied(f"DNS resolution failed for host '{host}'.")
        for address in addresses:
            self._assert_public_ip(address, host)

    def _resolve(self, host: str, port: int) -> list[str]:
        try:
            infos = self.resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise NavigationDenied(f"DNS resolution failed for host '{host}'.") from exc
        addresses: list[str] = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                addresses.append(sockaddr[0])
        # Preserve order but unique
        seen: set[str] = set()
        unique: list[str] = []
        for item in addresses:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def _assert_public_ip(self, address: str, host: str) -> None:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise NavigationDenied(f"Resolved address '{address}' for '{host}' is invalid.") from exc
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise NavigationDenied(
                f"Destination '{host}' resolves to non-public address '{address}', which is blocked."
            )
        # Cloud metadata link-local used by major providers (also covered by is_link_local for 169.254/16).
        if ip == ipaddress.ip_address("169.254.169.254") or ip == ipaddress.ip_address("fd00:ec2::254"):
            raise NavigationDenied("Cloud metadata addresses are blocked.")
