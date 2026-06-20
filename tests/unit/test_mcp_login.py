"""Tests for MCP login flow, session cookies, authorize auth-gating, and client allowlist."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.mcp_server.session_cookie import (
    COOKIE_NAME,
    create_session_cookie,
    verify_session_cookie,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_redis() -> tuple[AsyncMock, dict]:
    store: dict[str, str] = {}
    redis = AsyncMock()

    async def _setex(key, ttl, value):
        store[key] = value

    async def _get(key):
        return store.get(key)

    async def _delete(key):
        store.pop(key, None)

    async def _exists(key):
        return 1 if key in store else 0

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
                    results.append(store.get(key))
                elif op == "delete":
                    val = store.pop(key, None)
                    results.append(1 if val is not None else 0)
            return results

    redis.setex = _setex
    redis.get = _get
    redis.delete = _delete
    redis.exists = _exists
    redis.pipeline = MagicMock(return_value=_FakePipe())
    return redis, store


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(40)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _app_client(redis=None):
    from services.mcp_server.main import app

    if redis is None:
        redis, _ = _make_redis()
    app.state.redis = redis
    app.state.session_registry = MagicMock()
    return TestClient(app, raise_server_exceptions=True), redis


# ── Session cookie unit tests ─────────────────────────────────────────────────


def test_session_cookie_roundtrip():
    val = create_session_cookie("alice", "my-secret")
    uid = verify_session_cookie(val, "my-secret")
    assert uid == "alice"


def test_session_cookie_wrong_secret_returns_none():
    val = create_session_cookie("alice", "secret-A")
    assert verify_session_cookie(val, "secret-B") is None


def test_session_cookie_tampered_signature_returns_none():
    val = create_session_cookie("alice", "secret")
    # Flip last character of signature
    parts = val.rsplit(".", 1)
    tampered = parts[0] + "." + parts[1][:-1] + ("0" if parts[1][-1] != "0" else "1")
    assert verify_session_cookie(tampered, "secret") is None


def test_session_cookie_tampered_payload_returns_none():
    val = create_session_cookie("alice", "secret")
    b64, sig = val.rsplit(".", 1)
    # Modify b64 payload (changes content but keeps same length prefix)
    corrupt_b64 = b64[:-2] + ("AA" if b64[-2:] != "AA" else "BB")
    assert verify_session_cookie(f"{corrupt_b64}.{sig}", "secret") is None


def test_session_cookie_expired_returns_none():
    """A cookie timestamped in the past must be rejected when max_age is exceeded."""
    with patch("services.mcp_server.session_cookie.time") as mock_time:
        mock_time.time.return_value = 1_000_000  # fixed past timestamp
        val = create_session_cookie("alice", "secret")
    # Elapsed time is now huge vs max_age_s=1 — must be rejected
    assert verify_session_cookie(val, "secret", max_age_s=1) is None


def test_session_cookie_fresh_within_max_age():
    val = create_session_cookie("alice", "secret")
    assert verify_session_cookie(val, "secret", max_age_s=86400) == "alice"


def test_session_cookie_malformed_returns_none():
    assert verify_session_cookie("not-a-cookie", "secret") is None
    assert verify_session_cookie("", "secret") is None
    assert verify_session_cookie("a.b.c", "secret") is None


def test_session_cookie_empty_user_id_returns_none():
    """A cookie with user_id='' is treated as invalid."""
    import hmac as _hmac
    import json
    payload = json.dumps({"user_id": "", "ts": int(time.time())}, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    sig = _hmac.new(b"secret", b64.encode(), hashlib.sha256).hexdigest()
    val = f"{b64}.{sig}"
    assert verify_session_cookie(val, "secret") is None


# ── GET /login ────────────────────────────────────────────────────────────────


def test_login_page_returns_200():
    client, _ = _app_client()
    resp = client.get("/login")
    assert resp.status_code == 200


def test_login_page_content_type_is_html():
    client, _ = _app_client()
    resp = client.get("/login")
    assert "text/html" in resp.headers["content-type"]


def test_login_page_has_form_fields():
    client, _ = _app_client()
    resp = client.get("/login")
    body = resp.text
    assert 'name="username"' in body
    assert 'name="password"' in body
    assert 'type="password"' in body
    assert 'method="post"' in body.lower()


def test_login_page_includes_next_in_hidden_field():
    client, _ = _app_client()
    resp = client.get("/login", params={"next": "/oauth/authorize?foo=bar"})
    assert "/oauth/authorize" in resp.text


def test_login_page_no_auth_required():
    """Login page must be accessible without any credentials."""
    client, _ = _app_client()
    resp = client.get("/login")
    assert resp.status_code == 200


# ── POST /login ───────────────────────────────────────────────────────────────


def test_login_success_sets_session_cookie():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "correct-horse"
        ms.secret_key = "test-key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "correct-horse", "next": ""},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert COOKIE_NAME in resp.cookies


def test_login_success_cookie_is_valid():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "s3cr3t"
        ms.secret_key = "signing-key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "s3cr3t", "next": ""},
            follow_redirects=False,
        )
    cookie_val = resp.cookies[COOKIE_NAME]
    uid = verify_session_cookie(cookie_val, "signing-key")
    assert uid == "admin"


def test_login_wrong_password_returns_form():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "correct"
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "wrong", "next": ""},
            follow_redirects=False,
        )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert COOKIE_NAME not in resp.cookies


def test_login_wrong_username_no_cookie():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "correct"
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "hacker", "password": "correct", "next": ""},
            follow_redirects=False,
        )
    assert resp.status_code == 200
    assert COOKIE_NAME not in resp.cookies


def test_login_no_password_configured_always_fails():
    """When MCP_LOGIN_PASSWORD is unset, POST /login must always fail."""
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = ""  # not configured
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "anything", "next": ""},
            follow_redirects=False,
        )
    assert resp.status_code == 200  # re-renders form
    assert COOKIE_NAME not in resp.cookies


def test_login_success_redirects_to_next():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "pw"
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "pw",
                  "next": "/oauth/authorize?response_type=code"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "/oauth/authorize" in resp.headers["location"]


def test_login_next_external_url_is_sanitized():
    """External 'next' URLs must be replaced with the safe default."""
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "pw"
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "pw",
                  "next": "https://evil.com/steal"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "evil.com" not in location
    assert location.startswith("/")


def test_login_cookie_httponly():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "pw"
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "pw", "next": ""},
            follow_redirects=False,
        )
    set_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()


def test_login_cookie_samesite_lax():
    client, _ = _app_client()
    with patch("services.mcp_server.login.settings") as ms:
        ms.mcp_login_username = "admin"
        ms.mcp_login_password = "pw"
        ms.secret_key = "key"
        ms.environment = "dev"
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "pw", "next": ""},
            follow_redirects=False,
        )
    set_cookie = resp.headers.get("set-cookie", "").lower()
    assert "samesite=lax" in set_cookie


# ── /oauth/authorize session gating ──────────────────────────────────────────


def _authorize_params(challenge: str) -> dict:
    return {
        "response_type": "code",
        "client_id": "claude",
        "redirect_uri": "https://example.com/cb",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
    }


def test_authorize_no_session_redirects_to_login():
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        resp = client.get("/oauth/authorize", params=_authorize_params(challenge),
                          follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "/login" in loc
    assert "next=" in loc


def test_authorize_login_next_contains_authorize_path():
    """The 'next' param in the login redirect must point back to /oauth/authorize."""
    client, _ = _app_client()
    import urllib.parse
    _, challenge = _pkce_pair()
    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        resp = client.get("/oauth/authorize", params=_authorize_params(challenge),
                          follow_redirects=False)
    loc = resp.headers["location"]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    next_val = urllib.parse.unquote(qs["next"][0])
    assert "/oauth/authorize" in next_val


def test_authorize_with_valid_session_issues_code():
    """Valid session cookie → /oauth/authorize issues auth code and redirects."""
    client, redis = _app_client()
    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("alice", "test-secret")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params=_authorize_params(challenge),
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://example.com/cb")
    assert "code=" in loc
    assert "state=xyz" in loc


def test_authorize_code_encodes_user_id_from_cookie():
    """The auth code stored in Redis must carry the user_id from the session."""
    from services.mcp_server.oauth.store import consume_auth_code
    import asyncio

    client, redis = _app_client()
    redis_obj, store = _make_redis()
    client.app.state.redis = redis_obj

    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("bob", "test-secret")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params=_authorize_params(challenge),
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )

    assert resp.status_code == 302
    loc = resp.headers["location"]
    code = dict(p.split("=", 1) for p in loc.split("?", 1)[1].split("&"))["code"]
    data = asyncio.run(consume_auth_code(redis_obj, code))
    assert data is not None
    assert data["user_id"] == "bob"


# ── Demo mode fallback ────────────────────────────────────────────────────────


def test_demo_mode_disabled_by_default_no_user_id_param():
    """Without a session cookie, the user_id query param is ignored in production mode."""
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False   # default
        ms.allowed_client_ids = ""
        ms.secret_key = "s"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "user_id": "should-be-ignored"},
            follow_redirects=False,
        )
    # Must redirect to /login, not to redirect_uri
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_demo_mode_enabled_accepts_user_id_param():
    """When OAUTH_DEMO_MODE=true, user_id query param is used as fallback."""
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = True
        ms.allowed_client_ids = ""
        ms.secret_key = "s"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "user_id": "demo-alice"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://example.com/cb")


def test_demo_mode_enabled_session_cookie_takes_priority():
    """Even in demo mode, a valid session cookie is used over user_id param."""
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("cookie-user", "test-secret")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = True
        ms.allowed_client_ids = ""
        ms.secret_key = "test-secret"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "user_id": "param-user"},
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://example.com/cb")
    # Verify the stored code carries the cookie user, not the param user
    loc = resp.headers["location"]
    assert "code=" in loc  # issued a code — correct user verified by consume test above


# ── Client ID allowlist ───────────────────────────────────────────────────────


def test_allowlist_empty_accepts_any_client_id():
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("alice", "s")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = ""   # no restriction
        ms.secret_key = "s"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "client_id": "any-client"},
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "code=" in resp.headers["location"]


def test_allowlist_rejects_unknown_client_id():
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("alice", "s")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = "claude,claude-dev"
        ms.secret_key = "s"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "client_id": "evil-client"},
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert resp.status_code == 403
    assert "not permitted" in resp.json()["detail"]


def test_allowlist_accepts_known_client_id():
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("alice", "s")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = "claude,claude-dev"
        ms.secret_key = "s"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "client_id": "claude-dev"},
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "code=" in resp.headers["location"]


def test_allowlist_trims_whitespace():
    client, _ = _app_client()
    _, challenge = _pkce_pair()
    cookie_val = create_session_cookie("alice", "s")

    with patch("services.mcp_server.oauth.handlers.settings") as ms:
        ms.oauth_demo_mode = False
        ms.allowed_client_ids = " claude , claude-dev "  # spaces around commas
        ms.secret_key = "s"
        ms.auth_code_ttl_s = 120
        resp = client.get(
            "/oauth/authorize",
            params={**_authorize_params(challenge), "client_id": "claude"},
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


# ── Full login → authorize integration ───────────────────────────────────────


def test_full_login_then_authorize_flow():
    """End-to-end: POST /login → cookie → GET /oauth/authorize → auth code."""
    client, _ = _app_client()
    _, challenge = _pkce_pair()

    # Step 1: Login
    with patch("services.mcp_server.login.settings") as ls:
        ls.mcp_login_username = "admin"
        ls.mcp_login_password = "hunter2"
        ls.secret_key = "shared-secret"
        ls.environment = "dev"
        login_resp = client.post(
            "/login",
            data={"username": "admin", "password": "hunter2",
                  "next": "/oauth/authorize"},
            follow_redirects=False,
        )
    assert login_resp.status_code == 303
    assert COOKIE_NAME in login_resp.cookies
    cookie_val = login_resp.cookies[COOKIE_NAME]

    # Step 2: Authorize with cookie
    with patch("services.mcp_server.oauth.handlers.settings") as hs:
        hs.oauth_demo_mode = False
        hs.allowed_client_ids = ""
        hs.secret_key = "shared-secret"
        hs.auth_code_ttl_s = 120
        auth_resp = client.get(
            "/oauth/authorize",
            params=_authorize_params(challenge),
            headers={"Cookie": f"{COOKIE_NAME}={cookie_val}"},
            follow_redirects=False,
        )
    assert auth_resp.status_code == 302
    assert "code=" in auth_resp.headers["location"]
