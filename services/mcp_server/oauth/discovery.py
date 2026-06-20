"""RFC 8414 OAuth 2.0 Authorization Server Metadata endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


def _public_base(request: Request) -> str:
    """Return the public base URL, respecting reverse-proxy headers.

    Railway (and most proxies) terminate TLS at the edge and forward traffic
    as HTTP internally.  X-Forwarded-Proto carries the original scheme;
    X-Forwarded-Host carries the original host when it differs.
    """
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


@router.get("/.well-known/oauth-authorization-server")
async def oauth_discovery(request: Request) -> dict:
    """Return RFC 8414 server metadata for Claude custom connector discovery."""
    base = _public_base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
