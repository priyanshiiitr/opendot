"""OAuth connector unit tests — no live network."""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.connectors.credentials import InMemoryCredentialStore
from app.connectors.oauth import GitHubOAuthConnector, GmailOAuthConnector


def test_oauth_refuses_authorize_when_disabled():
    store = InMemoryCredentialStore()
    gh = GitHubOAuthConnector(
        store,
        Settings(oauth_connectors_enabled=False, github_oauth_client_id="id", github_oauth_client_secret="sec"),
    )
    with pytest.raises(RuntimeError, match="OAuth not ready|OAUTH"):
        gh.begin_authorize()


def test_oauth_authorize_url_when_enabled():
    store = InMemoryCredentialStore()
    gh = GitHubOAuthConnector(
        store,
        Settings(
            oauth_connectors_enabled=True,
            github_oauth_client_id="gh_client",
            github_oauth_client_secret="gh_secret",
            oauth_redirect_base="http://127.0.0.1:8000",
        ),
    )
    out = gh.begin_authorize()
    assert "github.com/login/oauth/authorize" in out["authorize_url"]
    assert "gh_client" in out["authorize_url"]
    assert out["state"]


def test_gmail_status_message_without_enable():
    gm = GmailOAuthConnector(InMemoryCredentialStore(), Settings(oauth_connectors_enabled=False))
    st = gm.status()
    assert st.connected is False
    assert "OAUTH_CONNECTORS_ENABLED" in st.message or "Refuses" in st.message


def test_disconnect_clears_token():
    store = InMemoryCredentialStore()
    store.set_secret("github_oauth_access_token", "tok")
    gh = GitHubOAuthConnector(
        store,
        Settings(oauth_connectors_enabled=True, github_oauth_client_id="a", github_oauth_client_secret="b"),
    )
    assert gh.status().connected is True
    st = asyncio.run(gh.disconnect())
    assert st.connected is False
    assert store.get_secret("github_oauth_access_token") is None
