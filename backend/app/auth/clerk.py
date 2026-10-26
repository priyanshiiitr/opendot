"""Optional Clerk JWT verification — off by default.

Enable only with AI_TEAMMATES_CLERK_ENABLED=true and a JWKS URL / issuer.
Never commits secrets. Local device pairing remains the default auth path.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import Request, urlopen


class ClerkAuthError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ClerkAuth:
    def __init__(
        self,
        *,
        enabled: bool = False,
        jwks_url: str = "",
        issuer: str = "",
        audience: str = "",
    ):
        self.enabled = enabled
        self.jwks_url = (jwks_url or "").strip()
        self.issuer = (issuer or "").strip()
        self.audience = (audience or "").strip()
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at = 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "clerk",
            "enabled": self.enabled,
            "configured": bool(self.jwks_url and self.issuer),
            "jwks_url_set": bool(self.jwks_url),
            "issuer_set": bool(self.issuer),
            "note": (
                "Disabled by default. Local pairing / loopback trust remains the primary auth path. "
                "Set AI_TEAMMATES_CLERK_ENABLED=true plus JWKS URL and issuer after creating a Clerk app."
            ),
        }

    def _require_ready(self) -> None:
        if not self.enabled:
            raise ClerkAuthError("Clerk auth is disabled (AI_TEAMMATES_CLERK_ENABLED=false).")
        if not self.jwks_url or not self.issuer:
            raise ClerkAuthError(
                "Clerk is enabled but AI_TEAMMATES_CLERK_JWKS_URL / AI_TEAMMATES_CLERK_ISSUER "
                "are missing. No tokens were accepted."
            )

    def _fetch_jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks and now - self._jwks_fetched_at < 3600:
            return self._jwks
        req = Request(self.jwks_url, headers={"User-Agent": "ai-teammates-clerk/1.0"})
        with urlopen(req, timeout=10) as resp:  # noqa: S310 — URL from trusted settings
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or "keys" not in data:
            raise ClerkAuthError("Clerk JWKS response missing keys.")
        self._jwks = data
        self._jwks_fetched_at = now
        return data

    def verify_bearer(self, token: str | None) -> dict[str, Any]:
        """Verify a Clerk session JWT. Returns claims on success."""
        self._require_ready()
        if not token:
            raise ClerkAuthError("Missing Bearer token for Clerk auth.")
        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as exc:
            raise ClerkAuthError(
                "PyJWT is required for Clerk verification. Run: pip install 'PyJWT[crypto]'"
            ) from exc
        try:
            jwks_client = PyJWKClient(self.jwks_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            options = {"verify_aud": bool(self.audience)}
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience or None,
                issuer=self.issuer,
                options=options,
            )
        except Exception as exc:
            raise ClerkAuthError(f"Clerk token verification failed: {exc}") from exc
        return claims if isinstance(claims, dict) else {"sub": str(claims)}
