"""RFC 8414 OAuth 2.0 Authorization Server Metadata endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/.well-known/oauth-authorization-server")
async def oauth_discovery(request: Request) -> dict:
    """Return RFC 8414 server metadata for Claude custom connector discovery."""
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
