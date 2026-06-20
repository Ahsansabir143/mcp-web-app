"""OAuth 2.0 PKCE authorization and token endpoints.

/oauth/authorize
  Derives the authenticated user from the signed session cookie set by POST /login.
  If no valid session cookie is present, redirects to /login?next=<authorize-url>.

  Demo fallback: when OAUTH_DEMO_MODE=true (disabled by default), the endpoint
  also accepts a user_id query parameter as a fallback.  This is only for local
  testing and must never be enabled in production.

/oauth/token
  Exchanges a PKCE auth code + code_verifier for a bearer token.
  Token and code are both stored in Redis with configured TTLs.

Client allowlist:
  Set ALLOWED_CLIENT_IDS to a comma-separated list of permitted client_id values.
  When empty (default), any client_id is accepted.
"""
from __future__ import annotations

import urllib.parse

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
from services.mcp_server.session_cookie import COOKIE_NAME, verify_session_cookie

router = APIRouter(prefix="/oauth")


def _allowed_clients() -> list[str]:
    """Parse ALLOWED_CLIENT_IDS setting into a list; empty list means allow all."""
    return [c.strip() for c in settings.allowed_client_ids.split(",") if c.strip()]


def _resolve_user(request: Request) -> str | None:
    """Return user_id from session cookie, or demo fallback, or None.

    Cookie takes priority over demo query param.
    """
    cookie_val = request.cookies.get(COOKIE_NAME)
    if cookie_val:
        uid = verify_session_cookie(cookie_val, settings.secret_key)
        if uid:
            return uid

    if settings.oauth_demo_mode:
        return request.query_params.get("user_id") or "demo-user"

    return None


@router.get("/authorize")
async def authorize(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    state: str = "",
) -> RedirectResponse:
    """PKCE authorization endpoint.

    Requires an authenticated session (session cookie from POST /login).
    Unauthenticated requests are redirected to /login with the full authorize
    URL preserved as the 'next' parameter so the flow resumes after login.
    """
    # Validate PKCE params before session check so malformed requests fail fast.
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

    # Validate client_id against allowlist (if configured).
    allowed = _allowed_clients()
    if allowed and client_id not in allowed:
        raise HTTPException(403, detail=f"client_id '{client_id}' is not permitted")

    # Require authenticated session.
    user_id = _resolve_user(request)
    if user_id is None:
        # Redirect to login, preserving the full authorize path+query as 'next'.
        path_with_query = request.url.path
        if request.url.query:
            path_with_query += "?" + request.url.query
        next_param = urllib.parse.quote(path_with_query, safe="")
        return RedirectResponse(url=f"/login?next={next_param}", status_code=302)

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
