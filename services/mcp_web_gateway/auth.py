"""JWT bearer-token validation for the MCP web gateway (resource-server role).

Validation priority:
1. RS256 / ES256 via JWKS URL — production path.
   Set OAUTH_JWKS_URL, or set OAUTH_ISSUER_URL and the JWKS URL is derived as
   {issuer}/.well-known/jwks.json.  When OAUTH_ISSUER_URL is set the ``iss``
   claim is also validated against it.
2. HS256 via OAUTH_JWT_SECRET — dev/test path only.
   Never set this in production; prefer JWKS.

On success returns :class:`TokenClaims`.
On failure raises ``HTTPException(401)`` with a ``WWW-Authenticate`` header
that points the client at the protected-resource metadata document.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Header, HTTPException, Request
from jose import JWTError, jwt

from .config import McpWebGatewaySettings, settings as _default_settings

logger = logging.getLogger(__name__)

# In-memory JWKS cache: url → (fetched_at, keys_list)
_JWKS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_JWKS_CACHE_TTL = 3600.0  # seconds; re-fetch JWKS at most once per hour


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    client_id: str              # from azp, client_id, or aud JWT claim
    scope: str                  # raw space-separated scope string
    granted_scopes: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TokenClaims":
        sub = str(payload.get("sub", ""))
        # RFC 8693: client identity may be in azp, client_id, or aud
        client_id = payload.get("azp") or payload.get("client_id") or payload.get("aud", "")
        if isinstance(client_id, list):
            client_id = client_id[0] if client_id else ""
        scope_str = payload.get("scope", "")
        return cls(
            sub=sub,
            client_id=str(client_id),
            scope=scope_str,
            granted_scopes=frozenset(scope_str.split()) if scope_str else frozenset(),
        )


# ── JWKS fetching ─────────────────────────────────────────────────────────────


async def _fetch_jwks(jwks_url: str) -> list[dict]:
    """Fetch JWKS keys with a simple in-memory TTL cache.

    Raises HTTPException(503) if the authorization server is unreachable so the
    client gets a clear "try again later" signal rather than an opaque 500.
    """
    cached = _JWKS_CACHE.get(jwks_url)
    if cached and (time.monotonic() - cached[0]) < _JWKS_CACHE_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(jwks_url)
            resp.raise_for_status()
            keys: list[dict] = resp.json().get("keys", [])
    except httpx.HTTPStatusError as exc:
        logger.error("JWKS endpoint returned %s for %s", exc.response.status_code, jwks_url)
        raise HTTPException(
            status_code=503,
            detail="Authorization server JWKS endpoint unavailable",
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("JWKS fetch error for %s: %s", jwks_url, exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to reach authorization server JWKS endpoint",
        ) from exc
    _JWKS_CACHE[jwks_url] = (time.monotonic(), keys)
    return keys


# ── JWT decoding ──────────────────────────────────────────────────────────────


async def _decode_jwt(token: str, cfg: McpWebGatewaySettings) -> dict[str, Any]:
    """Decode and validate the JWT.  Returns the raw payload dict.

    All JWTError details are logged server-side but are NOT propagated to the
    client to avoid leaking internal token structure information.
    """
    audience = cfg.oauth_audience or None
    verify_aud = bool(audience)
    issuer = cfg.oauth_issuer_url.rstrip("/") if cfg.oauth_issuer_url else None
    www_auth = _www_authenticate(cfg)

    # Path 1: JWKS (RS256 / ES256) — production
    if cfg.oauth_jwks_url or cfg.oauth_issuer_url:
        jwks_url = (
            cfg.oauth_jwks_url
            or f"{cfg.oauth_issuer_url.rstrip('/')}/.well-known/jwks.json"
        )
        keys = await _fetch_jwks(jwks_url)
        try:
            return jwt.decode(
                token,
                keys,
                algorithms=["RS256", "ES256", "RS384", "ES384"],
                audience=audience,
                issuer=issuer,
                options={"verify_aud": verify_aud, "verify_iss": bool(issuer)},
            )
        except JWTError as exc:
            logger.debug("JWT JWKS validation failed: %s", exc)
            raise HTTPException(
                status_code=401,
                detail="Token validation failed",
                headers={"WWW-Authenticate": www_auth},
            ) from exc

    # Path 2: HS256 static secret (dev/test only)
    if cfg.oauth_jwt_secret:
        try:
            return jwt.decode(
                token,
                cfg.oauth_jwt_secret,
                algorithms=["HS256"],
                audience=audience,
                options={"verify_aud": verify_aud},
            )
        except JWTError as exc:
            logger.debug("JWT HS256 validation failed: %s", exc)
            raise HTTPException(
                status_code=401,
                detail="Token validation failed",
                headers={"WWW-Authenticate": www_auth},
            ) from exc

    raise HTTPException(
        status_code=503,
        detail="Gateway OAuth validator not configured (set OAUTH_JWKS_URL or OAUTH_JWT_SECRET)",
    )


# ── FastAPI dependency ────────────────────────────────────────────────────────


def _www_authenticate(cfg: McpWebGatewaySettings) -> str:
    meta_url = f"{cfg.mcp_resource_url.rstrip('/')}/.well-known/oauth-protected-resource"
    return f'Bearer realm="mcp-web-gateway", resource_metadata="{meta_url}"'


async def require_token(
    request: Request,
    authorization: str = Header(default=""),
    cfg: McpWebGatewaySettings = None,
) -> TokenClaims:
    """FastAPI dependency that validates a Bearer JWT and returns its claims.

    Raises ``HTTPException(401)`` with a ``WWW-Authenticate`` header on failure.
    The ``WWW-Authenticate`` value contains a ``resource_metadata`` hint so
    compliant MCP clients can auto-discover the authorization server.
    """
    cfg = cfg or _default_settings
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header — expected 'Bearer <token>'",
            headers={"WWW-Authenticate": _www_authenticate(cfg)},
        )
    token = authorization[7:].strip()
    payload = await _decode_jwt(token, cfg)
    return TokenClaims.from_payload(payload)
