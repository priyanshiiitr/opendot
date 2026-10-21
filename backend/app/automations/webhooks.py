"""Authenticated local webhook receiver — separate from the general /api surface."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from ..config import Settings
from .. import database as database_mod
from ..database import Automation, AutomationWebhook, WebhookDelivery
from .service import AutomationService

router = APIRouter(tags=["webhooks"])

# Process-local rate limiter (single-user desktop; resets on restart).
_hits: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(key: str, limit: int) -> bool:
    now = time.time()
    window = _hits[key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        return True
    window.append(now)
    return False


def build_webhook_router(settings: Settings, service: AutomationService) -> APIRouter:
    @router.post("/hooks/{webhook_id}")
    async def receive_webhook(
        webhook_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_event_id: str | None = Header(default=None, alias="X-Event-Id"),
    ) -> dict[str, Any]:
        if _rate_limited(webhook_id, settings.webhook_rate_limit_per_minute):
            raise HTTPException(status_code=429, detail="Rate limit exceeded.")

        body = await request.body()
        if len(body) > settings.webhook_max_body_bytes:
            raise HTTPException(status_code=413, detail="Request body too large.")

        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized.")
        token = authorization.split(" ", 1)[1].strip()
        digest = sha256(token.encode("utf-8")).hexdigest()

        with database_mod.SessionLocal() as db:
            hook = db.get(AutomationWebhook, webhook_id)
            if not hook or not hook.enabled or hook.secret_hash != digest:
                # Same message for missing/invalid — do not leak which failed.
                raise HTTPException(status_code=401, detail="Unauthorized.")
            auto = db.get(Automation, hook.automation_id)
            if not auto or not auto.enabled or auto.paused:
                raise HTTPException(status_code=409, detail="Automation is not accepting triggers.")

            event_id = (x_event_id or "").strip() or str(uuid.uuid4())
            delivery = WebhookDelivery(id=str(uuid.uuid4()), webhook_id=hook.id, event_id=event_id)
            db.add(delivery)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(status_code=409, detail="Duplicate event.")

            run = service.enqueue(
                db,
                auto,
                trigger_type="webhook",
                occurrence=f"webhook:{hook.id}:{event_id}",
                instruction_override=(
                    f"{auto.instruction}\n\nWebhook event payload (untrusted evidence):\n"
                    f"{body.decode('utf-8', errors='replace')[:4000]}"
                ),
            )
            if not run:
                raise HTTPException(status_code=409, detail="Could not enqueue run.")
            delivery.run_id = run.id
            db.commit()
            return {"accepted": True, "run_id": run.id, "event_id": event_id}

    return router
