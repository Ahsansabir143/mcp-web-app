"""Login router for the MCP server OAuth authentication surface.

Endpoints:
  GET  /login          render sign-in form (redirects here from /oauth/authorize)
  POST /login          validate credentials, set signed session cookie, redirect

Security properties:
  - Credentials compared with secrets.compare_digest (constant-time, no timing attack)
  - Session cookie is HttpOnly, SameSite=Lax, Secure on non-dev environments
  - 'next' URL validated to be a local relative path (prevents open redirect)
  - Unsuccessful login re-renders form; never sets cookie
  - Login always fails if MCP_LOGIN_PASSWORD is unset (no implicit demo shortcut)
"""
from __future__ import annotations

import secrets
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from services.mcp_server.config import settings
from services.mcp_server.session_cookie import (
    COOKIE_MAX_AGE_S,
    COOKIE_NAME,
    create_session_cookie,
)

router = APIRouter()

_DEFAULT_NEXT = "/oauth/authorize"

_FORM_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MCP Server — Sign in</title>
  <style>
    *{{box-sizing:border-box}}
    body{{font-family:system-ui,sans-serif;margin:0;display:flex;align-items:center;
         justify-content:center;min-height:100vh;background:#f5f5f5}}
    .card{{background:#fff;border-radius:8px;padding:32px 28px;width:340px;
           box-shadow:0 1px 4px rgba(0,0,0,.15)}}
    h2{{margin:0 0 20px;font-size:1.1rem;color:#1a1a1a}}
    label{{display:block;margin-bottom:14px;font-size:.85rem;color:#444}}
    input{{display:block;width:100%;padding:9px 10px;margin-top:5px;border:1px solid #ccc;
           border-radius:4px;font-size:.95rem}}
    input:focus{{outline:2px solid #1a73e8;border-color:transparent}}
    button{{width:100%;padding:10px;margin-top:20px;background:#1a73e8;color:#fff;
            border:none;border-radius:4px;font-size:1rem;cursor:pointer}}
    button:hover{{background:#1557b0}}
    .err{{color:#c62828;font-size:.85rem;margin-bottom:14px;
          border-left:3px solid #c62828;padding-left:8px}}
  </style>
</head>
<body>
<div class="card">
  <h2>MCP Server — Sign in</h2>
  {error}
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{next}">
    <label>Username<input type="text" name="username" autocomplete="username" required></label>
    <label>Password<input type="password" name="password" autocomplete="current-password" required></label>
    <button type="submit">Sign in</button>
  </form>
</div>
</body>
</html>"""


def _safe_next(raw: str) -> str:
    """Return raw if it is a local relative path, else the default authorize path.

    Prevents open redirect: any next value with a scheme or netloc is rejected.
    """
    if not raw:
        return _DEFAULT_NEXT
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return _DEFAULT_NEXT
    return raw


def _render_form(next_url: str, error: str = "") -> HTMLResponse:
    error_html = f'<p class="err">{error}</p>' if error else ""
    html = _FORM_TEMPLATE.format(next=_safe_next(next_url), error=error_html)
    return HTMLResponse(content=html, status_code=200)


@router.get("/login")
async def login_get(request: Request, next: str = "") -> HTMLResponse:
    return _render_form(next)


@router.post("/login")
async def login_post(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """Validate credentials; on success set session cookie and redirect."""
    if not _credentials_valid(username, password):
        return _render_form(next, error="Invalid username or password.")

    safe_next = _safe_next(next)
    response = RedirectResponse(url=safe_next, status_code=303)
    cookie_val = create_session_cookie(user_id=username, secret=settings.secret_key)
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_val,
        max_age=COOKIE_MAX_AGE_S,
        httponly=True,
        samesite="lax",
        secure=(settings.environment != "dev"),
        path="/",
    )
    return response


def _credentials_valid(username: str, password: str) -> bool:
    """Constant-time comparison; always False if password env var is unset."""
    expected_user = settings.mcp_login_username
    expected_pass = settings.mcp_login_password
    if not expected_pass:
        # Guard: secrets.compare_digest requires non-empty strings on both sides.
        # Do the comparison anyway so we burn the same wall-clock time, but return False.
        secrets.compare_digest(username.encode(), expected_user.encode())
        secrets.compare_digest(b"", b"dummy")
        return False
    user_ok = secrets.compare_digest(username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(password.encode(), expected_pass.encode())
    return user_ok and pass_ok
