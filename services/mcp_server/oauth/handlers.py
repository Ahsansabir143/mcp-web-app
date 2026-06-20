"""OAuth 2.0 PKCE authorization and token endpoints.

Phase note: /oauth/authorize accepts ``user_id`` as a query parameter (internal
demo mode).  In production this endpoint would gate on a login session cookie and
derive the identity server-side.  The PKCE mechanics, code/token Redis storage, and
token exchange are production-ready; only the identity derivation is simplified.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from services.mcp_server.config import settings
from services.mcp_server.oauth.store import (
    consume_auth_code,
    generate_auth_code,
    generate_bearer_token,
    store_auth_code,
    store_bearer_token,
    verify_pkce_s256,
)

router = APIRouter(prefix="/oauth")


@router.get("/authorize")
async def authorize(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    state: str = "",
    user_id: str = "anonymous",
) -> RedirectResponse:
    """PKCE authorization endpoint — issues an auth code and redirects."""
    if response_type != "code":
        raise HTTPException(400, detail="only response_type=code is supported")
    if not client_id:
        raise HTTPException(400, detail="client_id is required")
    if not redirect_uri:
        raise HTTPException(400, detail="redirect_uri is required")
    if not code_challenge:
        raise HTTPException(400, detail="code_challenge is required (PKCE)")
    if code_challenge_method.upper() != "S256":
        raise HTTPException(400, detail="only code_challenge_method=S256 is supported")

    redis = request.app.state.redis
    code = generate_auth_code()
    await store_auth_code(
        redis,
        code,
        code_challenge=code_challenge,
        redirect_uri=redirect_uri,
        client_id=client_id,
        user_id=user_id,
        state=state,
        ttl_s=settings.auth_code_ttl_s,
    )

    redirect_url = f"{redirect_uri}?code={code}"
    if state:
        redirect_url += f"&state={state}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/token")
async def token(
    request: Request,
    grant_type: str = Form(default=""),
    code: str = Form(default=""),
    code_verifier: str = Form(default=""),
    redirect_uri: str = Form(default=""),
    client_id: str = Form(default=""),
) -> dict:
    """Exchange a PKCE auth code for a bearer token."""
    if grant_type != "authorization_code":
        raise HTTPException(400, detail="only grant_type=authorization_code is supported")
    if not code:
        raise HTTPException(400, detail="code is required")
    if not code_verifier:
        raise HTTPException(400, detail="code_verifier is required")

    redis = request.app.state.redis
    code_data = await consume_auth_code(redis, code)
    if code_data is None:
        raise HTTPException(400, detail="invalid_grant: code not found or expired")

    if not verify_pkce_s256(code_verifier, code_data["code_challenge"]):
        raise HTTPException(400, detail="invalid_grant: PKCE verification failed")

    if redirect_uri and redirect_uri != code_data["redirect_uri"]:
        raise HTTPException(400, detail="invalid_grant: redirect_uri mismatch")

    bearer = generate_bearer_token()
    await store_bearer_token(
        redis,
        bearer,
        user_id=code_data["user_id"],
        client_id=code_data["client_id"],
        scope="mcp",
        ttl_s=settings.access_token_ttl_s,
    )

    return {
        "access_token": bearer,
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_s,
    }
