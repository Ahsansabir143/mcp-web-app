"""Tests for MCP OAuth 2.0 PKCE flow and updated auth middleware."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.mcp_server.identity import McpIdentity
from services.mcp_server.oauth.store import (
    consume_auth_code,
    generate_auth_code,
    generate_bearer_token,
    lookup_bearer_token,
    store_auth_code,
    store_bearer_token,
    verify_pkce_s256,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pkce_pair() -> tuple[str, str]:
    """Generate a (code_verifier, code_challenge) S256 pair."""
    verifier = secrets.token_urlsafe(40)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _make_redis(stored: dict | None = None) -> AsyncMock:
    """Build a Redis mock that simulates setex/get/delete/exists/pipeline."""
    redis = AsyncMock()
    _store: dict[str, bytes] = {}

    async def _setex(key, ttl, value):
        _store[key] = value

    async def _get(key):
        return _store.get(key)

    async def _delete(*keys):
        for k in keys:
            _store.pop(k, None)

    async def _exists(key):
        return 1 if key in _store else 0

    class _FakePipe:
        def __init__(self):
            self._ops = []

        def get(self, key):
            self._ops.append(("get", key))
            return self

        def delete(self, key):
            self._ops.append(("delete", key))
            return self

        async def execute(self):
            results = []
            for op, key in self._ops:
                if op == "get":
                    results.append(_store.get(key))
                elif op == "delete":
                    val = _store.pop(key, None)
                    results.append(1 if val is not None else 0)
            return results

    redis.setex = _setex
    redis.get = _get
    redis.delete = _delete
    redis.exists = _exists
    redis.pipeline = MagicMock(return_value=_FakePipe())
    return redis


def _app_client(redis=None):
    """Return TestClient with mocked app.state.redis (no lifespan)."""
    from services.mcp_server.main import app

    if redis is None:
        redis = _make_redis()
    app.state.redis = redis
    app.state.session_registry = MagicMock()
    return TestClient(app, raise_server_exceptions=True), redis


# ── PKCE helper ───────────────────────────────────────────────────────────────


def test_verify_pkce_s256_correct():
    verifier, challenge = _pkce_pair()
    assert verify_pkce_s256(verifier, challenge) is True


def test_verify_pkce_s256_wrong_verifier():
    _, challenge = _pkce_pair()
    assert verify_pkce_s256("wrong-verifier", challenge) is False


def test_verify_pkce_s256_tampered_challenge():
    verifier, _ = _pkce_pair()
    assert verify_pkce_s256(verifier, "tampered") is False


# ── Token generators ──────────────────────────────────────────────────────────


def test_generate_auth_code_url_safe():
    code = generate_auth_code()
    assert isinstance(code, str)
    assert len(code) > 20
    assert all(c not in code for c in ["+", "/", "="])


def test_generate_bearer_token_longer():
    token = generate_bearer_token()
    assert len(token) > len(generate_auth_code())


# ── OAuth store (unit) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_and_consume_auth_code():
    redis = _make_redis()
    verifier, challenge = _pkce_pair()
    code = generate_auth_code()

    await store_auth_code(
        redis,
        code,
        code_challenge=challenge,
        redirect_uri="https://claude.ai/callback",
        client_id="claude",
        user_id="user-42",
        state="abc",
        ttl_s=120,
    )

    data = await consume_auth_code(redis, code)
    assert data is not None
    assert data["code_challenge"] == challenge
    assert data["user_id"] == "user-42"
    assert data["client_id"] == "claude"
    assert data["state"] == "abc"


@pytest.mark.asyncio
async def test_consume_auth_code_is_single_use():
    redis = _make_redis()
    code = generate_auth_code()
    verifier, challenge = _pkce_pair()

    await store_auth_code(
        redis, code,
        code_challenge=challenge, redirect_uri="https://example.com",
        client_id="c", user_id="u", state="", ttl_s=120,
    )
    await consume_auth_code(redis, code)

    # Second consume must return None
    result = await consume_auth_code(redis, code)
    assert result is None


@pytest.mark.asyncio
async def test_consume_missing_code_returns_none():
    redis = _make_redis()
    result = await consume_auth_code(redis, "nonexistent-code")
    assert result is None


@pytest.mark.asyncio
async def test_store_and_lookup_bearer_token():
    redis = _make_redis()
    token = generate_bearer_token()

    await store_bearer_token(
        redis, token,
        user_id="user-99", client_id="claude", scope="mcp", ttl_s=3600,
    )

    payload = await lookup_bearer_token(redis, token)
    assert payload is not None
    assert payload["user_id"] == "user-99"
    assert payload["scope"] == "mcp"


@pytest.mark.asyncio
async def test_lookup_missing_token_returns_none():
    redis = _make_redis()
    assert await lookup_bearer_token(redis, "no-such-token") is None


# ── RFC 8414 discovery endpoint ───────────────────────────────────────────────


def test_discovery_returns_required_fields():
    client, _ = _app_client()
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    body = resp.json()
    assert "issuer" in body
    assert "authorization_endpoint" in body
    assert "token_endpoint" in body
    assert "S256" in body["code_challenge_methods_supported"]
    assert "authorization_code" in body["grant_types_supported"]
    assert "none" in body["token_endpoint_auth_methods_supported"]


def test_discovery_urls_match_host():
    client, _ = _app_client()
    resp = client.get("/.well-known/oauth-authorization-server")
    body = resp.json()
    base = body["issuer"]
    assert body["authorization_endpoint"].startswith(base)
    assert body["token_endpoint"].startswith(base)
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["token_endpoint"].endswith("/oauth/token")


def test_discovery_uses_forwarded_proto_https():
    """When X-Forwarded-Proto: https is present (Railway proxy), issuer must use https://."""
    client, _ = _app_client()
    resp = client.get(
        "/.well-known/oauth-authorization-server",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "mcp-server-production-8d79.up.railway.app"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"].startswith("https://")
    assert body["authorization_endpoint"].startswith("https://")
    assert body["token_endpoint"].startswith("https://")


def test_discovery_forwarded_host_overrides_netloc():
    """X-Forwarded-Host must appear in the issuer when set."""
    client, _ = _app_client()
    resp = client.get(
        "/.well-known/oauth-authorization-server",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "custom.example.com"},
    )
    body = resp.json()
    assert "custom.example.com" in body["issuer"]
    assert body["issuer"] == "https://custom.example.com"


def test_discovery_falls_back_to_request_scheme_without_headers():
    """Without forwarded headers, the actual request scheme/host is used (local dev)."""
    client, _ = _app_client()
    resp = client.get("/.well-known/oauth-authorization-server")
    body = resp.json()
    # TestClient uses http://testserver — no forwarded headers present
    assert "testserver" in body["issuer"]


# ── /oauth/authorize ──────────────────────────────────────────────────────────


def test_authorize_rejects_missing_code_challenge():
    client, _ = _app_client()
    resp = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "claude",
            "redirect_uri": "https://example.com/cb",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "code_challenge" in resp.json()["detail"]


def test_authorize_rejects_plain_method():
    client, _ = _app_client()
    verifier, challenge = _pkce_pair()
    resp = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "claude",
            "redirect_uri": "https://example.com/cb",
            "code_challenge": challenge,
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "S256" in resp.json()["detail"]


def test_authorize_without_session_redirects_to_login():
    """Without a session cookie, /oauth/authorize must redirect to /login."""
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "claude",
                "redirect_uri": "https://example.com/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "mystate",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/login" in location
    assert "next=" in location


# ── /oauth/token ──────────────────────────────────────────────────────────────


def _do_authorize(client, redis, *, user_id="user-1", state="s") -> tuple[str, str]:
    """Run /oauth/authorize in demo mode and return (code, verifier).

    Uses OAUTH_DEMO_MODE=True so the token-exchange tests can obtain a code
    without needing a real login session.  The authenticated path (session cookie
    required) is fully tested in test_mcp_login.py.
    """
    verifier, challenge = _pkce_pair()
    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = True
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        ms.access_token_ttl_s = 3600
        resp = client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "claude",
                "redirect_uri": "https://example.com/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "user_id": user_id,
            },
            follow_redirects=False,
        )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    code = dict(p.split("=", 1) for p in loc.split("?", 1)[1].split("&"))["code"]
    return code, verifier


def test_token_exchange_succeeds():
    client, redis = _app_client()
    code, verifier = _do_authorize(client, redis)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://example.com/cb",
            "client_id": "claude",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["expires_in"] > 0


def test_token_exchange_wrong_verifier():
    client, redis = _app_client()
    code, _ = _do_authorize(client, redis)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": "wrong-verifier",
            "redirect_uri": "https://example.com/cb",
            "client_id": "claude",
        },
    )
    assert resp.status_code == 400
    assert "PKCE" in resp.json()["detail"]


def test_token_exchange_replayed_code_fails():
    client, redis = _app_client()
    code, verifier = _do_authorize(client, redis)

    # First exchange succeeds
    r1 = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://example.com/cb",
            "client_id": "claude",
        },
    )
    assert r1.status_code == 200

    # Replay must fail
    r2 = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://example.com/cb",
            "client_id": "claude",
        },
    )
    assert r2.status_code == 400
    assert "invalid_grant" in r2.json()["detail"]


def test_token_exchange_missing_code():
    client, _ = _app_client()
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code_verifier": "v"},
    )
    assert resp.status_code == 400
    assert "code" in resp.json()["detail"]


# ── Auth middleware ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bearer_auth_accepted():
    redis = _make_redis()
    token = generate_bearer_token()
    await store_bearer_token(
        redis, token, user_id="user-33", client_id="claude", scope="mcp", ttl_s=3600
    )

    from services.mcp_server.auth import verify_mcp_auth

    request = MagicMock()
    request.app.state.redis = redis

    identity = await verify_mcp_auth(
        request, authorization=f"Bearer {token}", x_api_key=""
    )
    assert identity.user_id == "user-33"
    assert identity.auth_method == "oauth"
    assert identity.is_oauth is True


@pytest.mark.asyncio
async def test_bearer_auth_invalid_token_raises_401():
    redis = _make_redis()

    from fastapi import HTTPException

    from services.mcp_server.auth import verify_mcp_auth

    request = MagicMock()
    request.app.state.redis = redis

    with pytest.raises(HTTPException) as exc_info:
        await verify_mcp_auth(request, authorization="Bearer bad-token", x_api_key="")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_api_key_auth_accepted():
    from services.mcp_server.auth import verify_mcp_auth

    request = MagicMock()

    with patch("services.mcp_server.auth.settings") as mock_settings:
        mock_settings.mcp_api_key = "valid-key"
        identity = await verify_mcp_auth(request, authorization="", x_api_key="valid-key")

    assert identity.auth_method == "api_key"
    assert identity.user_id == "api-key-user"
    assert identity.is_oauth is False


@pytest.mark.asyncio
async def test_no_credentials_raises_401():
    from fastapi import HTTPException

    from services.mcp_server.auth import verify_mcp_auth

    request = MagicMock()
    request.app.state.redis = _make_redis()

    with pytest.raises(HTTPException) as exc_info:
        await verify_mcp_auth(request, authorization="", x_api_key="")
    assert exc_info.value.status_code == 401


# ── McpIdentity ───────────────────────────────────────────────────────────────


def test_identity_is_frozen():
    identity = McpIdentity(user_id="u", client_id="c", auth_method="oauth")
    with pytest.raises((AttributeError, TypeError)):
        identity.user_id = "other"  # type: ignore[misc]


def test_identity_as_dict():
    identity = McpIdentity(user_id="u", client_id="c", auth_method="api_key", scope="mcp")
    d = identity.as_dict()
    assert d["user_id"] == "u"
    assert d["auth_method"] == "api_key"
    assert d["scope"] == "mcp"


def test_identity_is_oauth_false_for_api_key():
    identity = McpIdentity(user_id="u", client_id="c", auth_method="api_key")
    assert identity.is_oauth is False


# ── Redis key builders ────────────────────────────────────────────────────────


def test_oauth_redis_keys():
    from shared.redis.keys import RedisKeys

    assert RedisKeys.mcp_oauth_code("abc") == "mcp:oauth:code:abc"
    assert RedisKeys.mcp_oauth_token("tok") == "mcp:oauth:token:tok"
    assert RedisKeys.mcp_session("sid") == "mcp:session:sid"
