"""Trigger.dev (or generic webhook) external durable enqueue.

Does not replace in_process by default. Only starts when:
  AI_TEAMMATES_DURABLE_EXECUTION_BACKEND=trigger_http
  AI_TEAMMATES_DURABLE_EXTERNAL_BACKEND_ENABLED=true
  AI_TEAMMATES_TRIGGER_WEBHOOK_URL is set

Trigger.dev API mode (recommended):
  URL = https://api.trigger.dev/api/v1/tasks/ai-teammates-enqueue/trigger
  SECRET = tr_dev_sk_… (Authorization: Bearer)
  Body = { "payload": <JobEnvelope>, "options": { "idempotencyKey": … } }

Generic webhook mode:
  Body = JobEnvelope JSON; optional HMAC X-AI-Teammates-Signature
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.request import Request, urlopen

from ..config import Settings
from .jobs import FUTURE_EXTERNAL_BACKEND_REQUIREMENTS, ExternalDurableNotConfigured, JobEnvelope


def _is_trigger_api_url(url: str) -> bool:
    u = url.lower()
    return "api.trigger.dev" in u and "/api/v1/tasks/" in u and u.rstrip("/").endswith("/trigger")


class TriggerHttpDurableBackend:
    backend_name = "trigger_http"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._started = False

    def describe(self) -> dict[str, Any]:
        url = (self.settings.trigger_webhook_url or "").strip()
        url_set = bool(url)
        return {
            "backend": self.backend_name,
            "enabled_flag": self.settings.durable_external_backend_enabled,
            "webhook_configured": url_set,
            "trigger_api_mode": _is_trigger_api_url(url) if url_set else False,
            "implemented": True,
            "started": self._started,
            "future_requirements": list(FUTURE_EXTERNAL_BACKEND_REQUIREMENTS),
            "note": (
                "HTTP enqueue for Trigger.dev REST API or any webhook worker. "
                "Default runner remains in_process until you switch the backend name."
            ),
        }

    async def start(self) -> None:
        if not self.settings.durable_external_backend_enabled:
            raise ExternalDurableNotConfigured(
                "External durable backend disabled. "
                "Set AI_TEAMMATES_DURABLE_EXTERNAL_BACKEND_ENABLED=true only after "
                "configuring AI_TEAMMATES_TRIGGER_WEBHOOK_URL."
            )
        if not (self.settings.trigger_webhook_url or "").strip():
            raise ExternalDurableNotConfigured(
                "trigger_http backend requires AI_TEAMMATES_TRIGGER_WEBHOOK_URL. "
                "No remote jobs were created."
            )
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def enqueue(self, job: JobEnvelope) -> str:
        if not self._started:
            raise ExternalDurableNotConfigured("trigger_http backend is not started.")
        url = (self.settings.trigger_webhook_url or "").strip()
        secret = (self.settings.trigger_webhook_secret or "").strip()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ai-teammates-trigger-http/1.0",
            "X-Job-Idempotency-Key": job.idempotency_key or job.run_id,
        }

        if _is_trigger_api_url(url):
            if not secret:
                raise ExternalDurableNotConfigured(
                    "Trigger.dev API URL requires AI_TEAMMATES_TRIGGER_WEBHOOK_SECRET "
                    "(use your Development TRIGGER_SECRET_KEY / tr_dev_sk_…)."
                )
            headers["Authorization"] = f"Bearer {secret}"
            body_obj: dict[str, Any] = {"payload": job.as_dict()}
            if job.idempotency_key or job.run_id:
                body_obj["options"] = {"idempotencyKey": job.idempotency_key or job.run_id}
            body = json.dumps(body_obj).encode("utf-8")
        else:
            body = json.dumps(job.as_dict()).encode("utf-8")
            if secret:
                sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
                headers["X-AI-Teammates-Signature"] = sig

        req = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:  # noqa: S310 — URL from trusted settings
                raw = resp.read().decode("utf-8") or "{}"
                payload = json.loads(raw) if raw.strip().startswith("{") else {"ok": True, "raw": raw}
        except Exception as exc:
            raise ExternalDurableNotConfigured(f"Trigger webhook enqueue failed: {exc}") from exc
        return str(payload.get("id") or payload.get("run_id") or uuid.uuid4().hex)
