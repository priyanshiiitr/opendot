"""Request authorization helpers for optional remote access."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import DeviceSession
from .service import RemoteAccessService

PUBLIC_PREFIXES = (
    "/api/internal/computers/",  # guest agent token auth (not device pairing)
    "/health",
    "/ready",
    "/docs",
    "/openapi.json",
    "/hooks/",
)
PUBLIC_EXACT = {
    "/api/remote/status",
    "/api/remote/pairing/complete",
}
# Pairing start is not fully public — only loopback (checked below).


def client_ip(request: Request) -> str:
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def is_loopback(request: Request) -> bool:
    host = client_ip(request)
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Companion may also send X-AI-Teammates-Session (avoid putting tokens in query strings)
    return request.headers.get("x-ai-teammates-session")


def authorize_request(
    request: Request,
    db: Session,
    settings: Settings,
    remote: RemoteAccessService,
) -> DeviceSession | None:
    """Return session when authenticated; None when local trust applies.

    Raises HTTPException when remote access requires auth and it is missing/invalid.
    """
    path = request.url.path
    if path in PUBLIC_EXACT or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return None
    if path == "/api/remote/pairing/start":
        if not is_loopback(request):
            raise HTTPException(403, "Pairing must be started from the trusted local desktop (loopback).")
        return None
    if not settings.remote_access_enabled:
        return None
    if settings.local_trust_loopback and is_loopback(request):
        return None

    if not remote.rate.allow(f"auth:{client_ip(request)}", settings.auth_rate_limit_per_minute):
        raise HTTPException(429, "Too many requests.")

    token = extract_bearer(request)
    session = remote.resolve_session(db, token)
    if session is None:
        raise HTTPException(401, "Authentication required. Pair a device or use a valid session.")
    return session
