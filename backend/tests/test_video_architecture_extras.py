"""Video-architecture extras: Clerk, Trigger HTTP, OAuth stubs, Postgres note — all off by default."""

from __future__ import annotations

import asyncio

import pytest

from app.auth.clerk import ClerkAuth, ClerkAuthError
from app.automations.dispatcher import JobDispatcher
from app.automations.jobs import ExternalDurableNotConfigured, JobEnvelope
from app.automations.trigger_http import TriggerHttpDurableBackend
from app.automations.worker import AutomationWorker
from app.config import Settings
from app.connectors.credentials import InMemoryCredentialStore
from app.connectors.oauth import GmailOAuthConnector, GitHubOAuthConnector


def test_clerk_disabled_by_default():
    auth = ClerkAuth(enabled=False)
    assert auth.describe()["enabled"] is False
    with pytest.raises(ClerkAuthError, match="disabled"):
        auth.verify_bearer("x.y.z")


def test_clerk_enabled_without_jwks_refuses():
    auth = ClerkAuth(enabled=True, jwks_url="", issuer="")
    with pytest.raises(ClerkAuthError, match="JWKS|issuer|missing"):
        auth.verify_bearer("token")


def test_trigger_http_refuses_without_url():
    settings = Settings(
        durable_external_backend_enabled=True,
        durable_execution_backend="trigger_http",
        trigger_webhook_url="",
        automation_worker_enabled=False,
    )
    backend = TriggerHttpDurableBackend(settings)
    with pytest.raises(ExternalDurableNotConfigured, match="TRIGGER_WEBHOOK_URL|webhook"):
        asyncio.run(backend.start())


def test_trigger_http_enqueue_posts(monkeypatch):
    settings = Settings(
        durable_external_backend_enabled=True,
        durable_execution_backend="trigger_http",
        trigger_webhook_url="https://example.test/hooks/trigger",
        trigger_webhook_secret="sekrit",
        automation_worker_enabled=False,
    )
    backend = TriggerHttpDurableBackend(settings)
    captured: dict = {}

    class _Resp:
        def read(self):
            return b'{"id":"run_123"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["sig"] = req.headers.get("X-ai-teammates-signature") or req.headers.get(
            "X-AI-Teammates-Signature"
        )
        return _Resp()

    monkeypatch.setattr("app.automations.trigger_http.urlopen", fake_urlopen)
    asyncio.run(backend.start())
    job_id = asyncio.run(
        backend.enqueue(JobEnvelope(job_type="automation_run", run_id="r1", teammate_id=1))
    )
    assert job_id == "run_123"
    assert "example.test" in captured["url"]
    assert captured["sig"]


def test_job_dispatcher_lists_trigger_http():
    settings = Settings(automation_worker_enabled=False, durable_execution_backend="in_process")
    worker = AutomationWorker(settings, sessionmaker_stub(), runtime=None, service=None)  # type: ignore[arg-type]
    # Avoid real sessionmaker — describe only
    dispatcher = JobDispatcher(settings, worker)
    desc = dispatcher.describe()
    assert "trigger_http" in desc["backends"]
    assert desc["active"] == "in_process"


def sessionmaker_stub():
    class _S:
        def __call__(self, *a, **k):
            raise RuntimeError("session not needed for describe")

    return _S()


def test_oauth_refuse_without_enable():
    settings = Settings(oauth_connectors_enabled=False)
    gh = GitHubOAuthConnector(InMemoryCredentialStore(), settings)
    st = asyncio.run(gh.configure("unused", []))
    assert "authorize" in st.message.lower() or st.connected is False
    assert st.connected is False


def test_oauth_ready_but_not_connected():
    settings = Settings(
        oauth_connectors_enabled=True,
        github_oauth_client_id="cid",
        github_oauth_client_secret="csec",
    )
    gh = GitHubOAuthConnector(InMemoryCredentialStore(), settings)
    st = gh.status()
    assert st.token_configured is True
    assert st.connected is False
    assert "authorize" in st.message.lower() or "configured" in st.message.lower()
    gm = GmailOAuthConnector(InMemoryCredentialStore(), Settings(oauth_connectors_enabled=False))
    assert gm.status().connected is False


def test_trigger_api_mode_enqueue(monkeypatch):
    settings = Settings(
        durable_external_backend_enabled=True,
        durable_execution_backend="trigger_http",
        trigger_webhook_url="https://api.trigger.dev/api/v1/tasks/ai-teammates-enqueue/trigger",
        trigger_webhook_secret="tr_dev_sk_test",
        automation_worker_enabled=False,
    )
    backend = TriggerHttpDurableBackend(settings)
    captured: dict = {}

    class _Resp:
        def read(self):
            return b'{"id":"run_api_1"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["auth"] = req.headers.get("Authorization") or req.headers.get("authorization")
        return _Resp()

    monkeypatch.setattr("app.automations.trigger_http.urlopen", fake_urlopen)
    asyncio.run(backend.start())
    job_id = asyncio.run(
        backend.enqueue(JobEnvelope(job_type="automation_run", run_id="r1", teammate_id=1))
    )
    assert job_id == "run_api_1"
    assert captured["auth"] == "Bearer tr_dev_sk_test"
    assert b'"payload"' in captured["body"]
