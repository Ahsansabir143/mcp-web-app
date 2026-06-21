"""Tests for the MCP web gateway service.

Covers all five required areas:
1. Unauthenticated request → 401 + WWW-Authenticate header with metadata hint
2. Protected-resource metadata endpoint shape
3. JWT token validation — success and failure paths
4. Scope denial — blocked tools, missing scope
5. Allowed tool forwarding — proxied to internal MCP; blocked tools never reach proxy
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

# ── JWT helpers ───────────────────────────────────────────────────────────────

_TEST_SECRET = "test-hs256-secret-for-unit-tests-only"
_TEST_AUDIENCE = "mcp-web-gateway"
_FULL_SCOPE = "mcp:tools:read mcp:account:read mcp:strategy:read"
_TOOLS_SCOPE = "mcp:tools:read"
_ACCOUNT_SCOPE = "mcp:account:read"


def _make_token(
    sub: str = "user|test-123",
    client_id: str = "test-client",
    scope: str = _FULL_SCOPE,
    audience: str = _TEST_AUDIENCE,
    exp_offset: int = 3600,
) -> str:
    payload = {
        "sub": sub,
        "azp": client_id,
        "scope": scope,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")


def _expired_token() -> str:
    return _make_token(exp_offset=-120)


def _wrong_audience_token() -> str:
    return _make_token(audience="wrong-audience")


# ── App fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
def patched_settings(monkeypatch):
    """Patch the gateway settings to use HS256 test secret (no JWKS needed)."""
    monkeypatch.setenv("OAUTH_JWT_SECRET", _TEST_SECRET)
    monkeypatch.setenv("OAUTH_AUDIENCE", _TEST_AUDIENCE)
    monkeypatch.setenv("OAUTH_JWKS_URL", "")
    monkeypatch.setenv("OAUTH_ISSUER_URL", "")
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://gateway.example.com")
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVERS", "https://auth.example.com")
    monkeypatch.setenv("MCP_INTERNAL_URL", "http://internal-mcp:8006")
    monkeypatch.setenv("MCP_INTERNAL_API_KEY", "internal-test-key")
    # Return a fresh settings object built from these env vars
    from services.mcp_web_gateway.config import McpWebGatewaySettings
    return McpWebGatewaySettings()


@pytest.fixture
def client(patched_settings):
    """Synchronous TestClient for the gateway app with settings patched."""
    from services.mcp_web_gateway import auth as auth_mod
    from services.mcp_web_gateway import main as main_mod
    from services.mcp_web_gateway import config as cfg_mod

    with (
        patch.object(cfg_mod, "settings", patched_settings),
        patch.object(auth_mod, "_default_settings", patched_settings),
        patch.object(main_mod, "settings", patched_settings),
    ):
        yield TestClient(main_mod.app, raise_server_exceptions=False)


# ═════════════════════════════════════════════════════════════════════════════
# 1. UNAUTHENTICATED REQUEST → 401 + WWW-Authenticate
# ═════════════════════════════════════════════════════════════════════════════


def test_sse_no_token_returns_401(client):
    resp = client.get("/sse")
    assert resp.status_code == 401


def test_sse_no_token_has_www_authenticate_header(client):
    resp = client.get("/sse")
    www_auth = resp.headers.get("www-authenticate", "")
    assert "Bearer" in www_auth
    assert "oauth-protected-resource" in www_auth


def test_sse_malformed_header_returns_401(client):
    resp = client.get("/sse", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


def test_messages_unknown_session_returns_404(client):
    """POST /messages with no matching session returns 404 (not 401)."""
    resp = client.post(
        "/messages?session_id=does-not-exist",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# 2. PROTECTED-RESOURCE METADATA
# ═════════════════════════════════════════════════════════════════════════════


def test_metadata_no_auth_required(client):
    """/.well-known/oauth-protected-resource is publicly accessible."""
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200


def test_metadata_has_resource_field(client):
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert "resource" in body
    assert body["resource"] == "https://gateway.example.com"


def test_metadata_has_authorization_servers(client):
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert "authorization_servers" in body
    assert isinstance(body["authorization_servers"], list)
    assert "https://auth.example.com" in body["authorization_servers"]


def test_metadata_bearer_methods_header_only(client):
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["bearer_methods_supported"] == ["header"]


def test_metadata_scopes_include_all_three(client):
    body = client.get("/.well-known/oauth-protected-resource").json()
    scopes = set(body["scopes_supported"])
    assert "mcp:tools:read" in scopes
    assert "mcp:account:read" in scopes
    assert "mcp:strategy:read" in scopes


def test_metadata_no_extra_scopes(client):
    """Gateway must not advertise write or control scopes."""
    body = client.get("/.well-known/oauth-protected-resource").json()
    scopes = set(body["scopes_supported"])
    for s in scopes:
        assert "write" not in s
        assert "trade" not in s
        assert "control" not in s


# ═════════════════════════════════════════════════════════════════════════════
# 3. TOKEN VALIDATION
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_valid_token_accepted(patched_settings):
    """A valid JWT is accepted by the require_token dependency."""
    from services.mcp_web_gateway import auth as auth_mod
    from services.mcp_web_gateway.auth import TokenClaims

    token = _make_token()
    request = MagicMock()
    with patch.object(auth_mod, "_default_settings", patched_settings):
        claims = await auth_mod.require_token(
            request, authorization=f"Bearer {token}", cfg=patched_settings
        )
    assert isinstance(claims, TokenClaims)
    assert claims.sub == "user|test-123"
    assert "mcp:tools:read" in claims.granted_scopes
    assert "mcp:account:read" in claims.granted_scopes
    assert "mcp:strategy:read" in claims.granted_scopes


@pytest.mark.asyncio
async def test_expired_token_rejected(patched_settings):
    from services.mcp_web_gateway import auth as auth_mod
    from fastapi import HTTPException
    with patch.object(auth_mod, "_default_settings", patched_settings):
        with pytest.raises(HTTPException) as exc:
            await auth_mod.require_token(
                MagicMock(), authorization=f"Bearer {_expired_token()}", cfg=patched_settings
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_string_rejected(patched_settings):
    from services.mcp_web_gateway import auth as auth_mod
    from fastapi import HTTPException
    with patch.object(auth_mod, "_default_settings", patched_settings):
        with pytest.raises(HTTPException) as exc:
            await auth_mod.require_token(
                MagicMock(), authorization="Bearer not.a.jwt", cfg=patched_settings
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_rejected(patched_settings):
    from services.mcp_web_gateway import auth as auth_mod
    from fastapi import HTTPException
    with patch.object(auth_mod, "_default_settings", patched_settings):
        with pytest.raises(HTTPException) as exc:
            await auth_mod.require_token(
                MagicMock(), authorization=f"Bearer {_wrong_audience_token()}", cfg=patched_settings
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_tampered_signature_rejected(patched_settings):
    from services.mcp_web_gateway import auth as auth_mod
    from fastapi import HTTPException
    token = _make_token()
    header, payload, _ = token.rsplit(".", 2)
    tampered = f"{header}.{payload}.invalidsig"
    with patch.object(auth_mod, "_default_settings", patched_settings):
        with pytest.raises(HTTPException) as exc:
            await auth_mod.require_token(
                MagicMock(), authorization=f"Bearer {tampered}", cfg=patched_settings
            )
    assert exc.value.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 4. SCOPE AND POLICY ENFORCEMENT (unit-level, no HTTP needed)
# ═════════════════════════════════════════════════════════════════════════════


def test_blocked_tool_raises_policy_denied():
    from services.mcp_web_gateway.policy import check_tool_call, PolicyDenied
    full = frozenset(_FULL_SCOPE.split())
    with pytest.raises(PolicyDenied) as exc:
        check_tool_call("request_paper_trade", full)
    assert "not exposed" in exc.value.reason


def test_update_strategy_state_is_blocked():
    from services.mcp_web_gateway.policy import check_tool_call, PolicyDenied
    full = frozenset(_FULL_SCOPE.split())
    with pytest.raises(PolicyDenied):
        check_tool_call("update_strategy_state", full)


def test_simulate_tools_are_blocked():
    from services.mcp_web_gateway.policy import check_tool_call, PolicyDenied, BLOCKED_TOOLS
    full = frozenset(_FULL_SCOPE.split())
    for tool in BLOCKED_TOOLS:
        with pytest.raises(PolicyDenied):
            check_tool_call(tool, full)


def test_all_blocked_tools_not_in_allowed_set():
    from services.mcp_web_gateway.policy import BLOCKED_TOOLS, ALLOWED_TOOLS
    assert BLOCKED_TOOLS.isdisjoint(ALLOWED_TOOLS), "blocked and allowed sets must not overlap"


def test_missing_account_scope_denied():
    from services.mcp_web_gateway.policy import check_tool_call, PolicyDenied
    # token has only tools:read, not account:read
    scopes = frozenset(["mcp:tools:read"])
    with pytest.raises(PolicyDenied) as exc:
        check_tool_call("get_account_balances", scopes)
    assert "scope" in exc.value.reason.lower()


def test_missing_strategy_scope_denied():
    from services.mcp_web_gateway.policy import check_tool_call, PolicyDenied
    scopes = frozenset(["mcp:tools:read", "mcp:account:read"])
    with pytest.raises(PolicyDenied) as exc:
        check_tool_call("list_strategies", scopes)
    assert "scope" in exc.value.reason.lower()


def test_all_allowed_tools_pass_with_full_scope():
    from services.mcp_web_gateway.policy import check_tool_call, ALLOWED_TOOLS
    full = frozenset(_FULL_SCOPE.split())
    for tool in ALLOWED_TOOLS:
        check_tool_call(tool, full)  # must not raise


def test_unknown_tool_denied():
    from services.mcp_web_gateway.policy import check_tool_call, PolicyDenied
    full = frozenset(_FULL_SCOPE.split())
    with pytest.raises(PolicyDenied):
        check_tool_call("some_unknown_tool", full)


# ═════════════════════════════════════════════════════════════════════════════
# 5. TOOL FORWARDING (mocked proxy)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_allowed_tool_forwarded_to_proxy():
    """get_stream_health with full scope is proxied; result is enqueued on session."""
    from services.mcp_web_gateway.auth import TokenClaims
    from services.mcp_web_gateway import session as sess_mod
    from services.mcp_web_gateway.main import _handle_tools_call

    claims = TokenClaims(
        sub="user|42",
        client_id="test-client",
        scope=_FULL_SCOPE,
        granted_scopes=frozenset(_FULL_SCOPE.split()),
    )
    session = sess_mod.GatewaySession(session_id="s1", claims=claims)
    session.mark_initialized()

    upstream_response = {
        "jsonrpc": "2.0", "id": 2,
        "result": {
            "content": [{"type": "text", "text": '{"overall_healthy": true}'}],
            "isError": False,
        },
    }
    body = {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "get_stream_health", "arguments": {}},
    }

    with patch("services.mcp_web_gateway.main.InternalMcpClient") as MockProxy:
        instance = MockProxy.return_value
        instance.call_tool = AsyncMock(return_value=upstream_response)
        await _handle_tools_call(session, body, msg_id=5)

    assert not session.queue.empty()
    queued = await session.queue.get()
    assert queued["id"] == 5
    assert "result" in queued
    assert "error" not in queued
    instance.call_tool.assert_called_once_with("get_stream_health", {})


@pytest.mark.asyncio
async def test_blocked_tool_denied_without_touching_proxy():
    """request_paper_trade is blocked; the proxy is never called."""
    from services.mcp_web_gateway.auth import TokenClaims
    from services.mcp_web_gateway import session as sess_mod
    from services.mcp_web_gateway.main import _handle_tools_call

    claims = TokenClaims(
        sub="user|42",
        client_id="test-client",
        scope=_FULL_SCOPE,
        granted_scopes=frozenset(_FULL_SCOPE.split()),
    )
    session = sess_mod.GatewaySession(session_id="s2", claims=claims)
    body = {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {
            "name": "request_paper_trade",
            "arguments": {"strategy_id": "x", "symbol": "BTCUSDT", "side": "BUY"},
        },
    }

    with patch("services.mcp_web_gateway.main.InternalMcpClient") as MockProxy:
        instance = MockProxy.return_value
        instance.call_tool = AsyncMock()
        await _handle_tools_call(session, body, msg_id=7)

    queued = await session.queue.get()
    assert queued["id"] == 7
    assert "error" in queued
    assert "denied" in queued["error"]["message"].lower()
    # Proxy must never have been called
    instance.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_scope_insufficient_denied_without_touching_proxy():
    """Token with only mcp:tools:read cannot call get_account_balances."""
    from services.mcp_web_gateway.auth import TokenClaims
    from services.mcp_web_gateway import session as sess_mod
    from services.mcp_web_gateway.main import _handle_tools_call

    claims = TokenClaims(
        sub="user|42",
        client_id="test-client",
        scope=_TOOLS_SCOPE,
        granted_scopes=frozenset(_TOOLS_SCOPE.split()),
    )
    session = sess_mod.GatewaySession(session_id="s3", claims=claims)
    body = {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "get_account_balances", "arguments": {"account_id": "abc"}},
    }

    with patch("services.mcp_web_gateway.main.InternalMcpClient") as MockProxy:
        instance = MockProxy.return_value
        instance.call_tool = AsyncMock()
        await _handle_tools_call(session, body, msg_id=9)

    queued = await session.queue.get()
    assert queued["id"] == 9
    assert "error" in queued
    instance.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_upstream_error_propagated_as_error_response():
    """If the proxy raises UpstreamError, the client receives an error response."""
    from services.mcp_web_gateway.auth import TokenClaims
    from services.mcp_web_gateway import session as sess_mod
    from services.mcp_web_gateway.main import _handle_tools_call
    from services.mcp_web_gateway.proxy import UpstreamError

    claims = TokenClaims(
        sub="user|42",
        client_id="test-client",
        scope=_FULL_SCOPE,
        granted_scopes=frozenset(_FULL_SCOPE.split()),
    )
    session = sess_mod.GatewaySession(session_id="s4", claims=claims)
    body = {
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "get_symbol_snapshot", "arguments": {"symbol": "BTCUSDT"}},
    }

    with patch("services.mcp_web_gateway.main.InternalMcpClient") as MockProxy:
        instance = MockProxy.return_value
        instance.call_tool = AsyncMock(side_effect=UpstreamError("internal timeout"))
        await _handle_tools_call(session, body, msg_id=11)

    queued = await session.queue.get()
    assert queued["id"] == 11
    assert "error" in queued
    assert "timeout" in queued["error"]["message"].lower()


@pytest.mark.asyncio
async def test_tools_list_returns_only_allowed_tools():
    """_handle_tools_list filters out write/simulation tools from the internal response."""
    from services.mcp_web_gateway.auth import TokenClaims
    from services.mcp_web_gateway import session as sess_mod
    from services.mcp_web_gateway.main import _handle_tools_list
    from services.mcp_web_gateway.policy import ALLOWED_TOOLS, BLOCKED_TOOLS

    claims = TokenClaims(
        sub="user|42", client_id="test-client",
        scope=_FULL_SCOPE, granted_scopes=frozenset(_FULL_SCOPE.split()),
    )
    session = sess_mod.GatewaySession(session_id="s5", claims=claims)

    # Simulate internal server returning both allowed and blocked tools
    all_tools = [{"name": n, "description": ""} for n in list(ALLOWED_TOOLS) + list(BLOCKED_TOOLS)]

    with patch("services.mcp_web_gateway.main.InternalMcpClient") as MockProxy:
        instance = MockProxy.return_value
        instance.get_filtered_tools = AsyncMock(
            return_value=[t for t in all_tools if t["name"] in ALLOWED_TOOLS]
        )
        await _handle_tools_list(session, msg_id=3)

    queued = await session.queue.get()
    assert queued["id"] == 3
    returned_names = {t["name"] for t in queued["result"]["tools"]}
    assert returned_names.issubset(ALLOWED_TOOLS)
    assert returned_names.isdisjoint(BLOCKED_TOOLS)


# ═════════════════════════════════════════════════════════════════════════════
# Health endpoint
# ═════════════════════════════════════════════════════════════════════════════


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["service"] == "mcp-web-gateway"
