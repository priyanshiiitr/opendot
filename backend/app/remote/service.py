"""Device pairing and session management — hashes only; codes/tokens shown once."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..database import DeviceSession, PairingChallenge, SecurityAuditEvent


class RemoteAccessError(Exception):
    def __init__(self, reason: str, status_code: int = 400):
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


class RemoteAccessService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.rate = RateLimiter()

    def audit(self, db: Session, event_type: str, detail: str = "", device_session_id: str | None = None) -> None:
        db.add(
            SecurityAuditEvent(
                id=str(uuid.uuid4()),
                event_type=event_type,
                detail=detail[:500],
                device_session_id=device_session_id,
            )
        )
        db.commit()

    def start_pairing(self, db: Session, *, client_key: str) -> dict[str, Any]:
        if not self.settings.remote_access_enabled:
            raise RemoteAccessError("Remote access is disabled. Enable AI_TEAMMATES_REMOTE_ACCESS_ENABLED explicitly.")
        if not self.rate.allow(f"pair-start:{client_key}", self.settings.pairing_rate_limit_per_minute):
            raise RemoteAccessError("Too many pairing attempts. Try again later.", 429)
        code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].upper()
        row = PairingChallenge(
            id=str(uuid.uuid4()),
            code_hash=_hash_secret(code),
            expires_at=_now() + timedelta(seconds=self.settings.pairing_code_ttl_seconds),
        )
        db.add(row)
        db.commit()
        self.audit(db, "pairing_started", f"challenge={row.id}")
        return {
            "challenge_id": row.id,
            "code": code,  # shown once to the owner on the trusted desktop
            "expires_at": row.expires_at,
            "ttl_seconds": self.settings.pairing_code_ttl_seconds,
            "message": "Enter this code on the companion device. It will not be shown again.",
        }

    def complete_pairing(
        self,
        db: Session,
        *,
        code: str,
        device_name: str,
        client_key: str,
    ) -> dict[str, Any]:
        if not self.settings.remote_access_enabled:
            raise RemoteAccessError("Remote access is disabled.", 403)
        if not self.rate.allow(f"pair-complete:{client_key}", self.settings.pairing_rate_limit_per_minute):
            raise RemoteAccessError("Too many pairing attempts. Try again later.", 429)
        cleaned = (code or "").strip().upper()
        if len(cleaned) < 6:
            raise RemoteAccessError("Invalid pairing code.")
        digest = _hash_secret(cleaned)
        row = (
            db.query(PairingChallenge)
            .filter(PairingChallenge.code_hash == digest, PairingChallenge.consumed_at.is_(None))
            .order_by(PairingChallenge.created_at.desc())
            .first()
        )
        if not row:
            self.audit(db, "pairing_failed", "invalid_or_unknown_code")
            raise RemoteAccessError("Invalid or expired pairing code.", 401)
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp < _now():
            self.audit(db, "pairing_failed", "expired")
            raise RemoteAccessError("Pairing code expired.", 401)
        row.consumed_at = _now()
        token = secrets.token_urlsafe(32)
        session = DeviceSession(
            id=str(uuid.uuid4()),
            device_name=(device_name or "companion")[:120],
            token_hash=_hash_secret(token),
            expires_at=_now() + timedelta(hours=self.settings.session_ttl_hours),
            last_seen_at=_now(),
        )
        db.add(session)
        db.commit()
        self.audit(db, "pairing_completed", f"device={session.device_name}", session.id)
        return {
            "session_id": session.id,
            "device_name": session.device_name,
            "token": token,  # shown once — store in sessionStorage on companion
            "expires_at": session.expires_at,
            "message": "Store this session token securely. It will not be shown again.",
        }

    def resolve_session(self, db: Session, token: str | None) -> DeviceSession | None:
        if not token:
            return None
        digest = _hash_secret(token.strip())
        row = db.query(DeviceSession).filter(DeviceSession.token_hash == digest).first()
        if not row or row.revoked:
            return None
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if exp < _now():
            return None
        row.last_seen_at = _now()
        db.commit()
        return row

    def list_devices(self, db: Session) -> list[dict[str, Any]]:
        rows = db.query(DeviceSession).order_by(DeviceSession.created_at.desc()).limit(50).all()
        return [
            {
                "id": r.id,
                "device_name": r.device_name,
                "revoked": r.revoked,
                "expires_at": r.expires_at,
                "last_seen_at": r.last_seen_at,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    def revoke_device(self, db: Session, session_id: str) -> DeviceSession:
        row = db.get(DeviceSession, session_id)
        if not row:
            raise RemoteAccessError("Device session not found.", 404)
        row.revoked = True
        db.commit()
        self.audit(db, "device_revoked", f"session={session_id}", session_id)
        db.refresh(row)
        return row

    def logout(self, db: Session, session: DeviceSession) -> None:
        session.revoked = True
        db.commit()
        self.audit(db, "logout", device_session_id=session.id)

    def constant_time_compare(self, a: str, b: str) -> bool:
        return hmac.compare_digest(a, b)
