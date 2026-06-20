"""HMAC-SHA256 signed session cookie for MCP login state.

Cookie format:   BASE64URL(json_payload) "." HMAC-SHA256-hexdigest

Payload JSON:    {"user_id": str, "ts": int}

The HMAC key is settings.secret_key — a high-entropy string that must be set
as a Railway environment variable (SECRET_KEY).  Changing the key invalidates
all existing login sessions.

No third-party JWT library required.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

COOKIE_NAME = "mcp_login"
COOKIE_MAX_AGE_S: int = 86_400  # 24 h default


def _b64(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).rstrip(b"=").decode()


def _unb64(data: str) -> str:
    # Restore stripped padding before decoding
    padding = (4 - len(data) % 4) % 4
    return base64.urlsafe_b64decode(data + "=" * padding).decode()


def _sign(b64_payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), b64_payload.encode(), hashlib.sha256).hexdigest()


def create_session_cookie(user_id: str, secret: str) -> str:
    """Return a signed cookie value for the given user."""
    payload = json.dumps({"user_id": user_id, "ts": int(time.time())}, separators=(",", ":"))
    b64 = _b64(payload)
    sig = _sign(b64, secret)
    return f"{b64}.{sig}"


def verify_session_cookie(
    cookie_value: str,
    secret: str,
    max_age_s: int = COOKIE_MAX_AGE_S,
) -> str | None:
    """Return user_id if the cookie signature is valid and the session is fresh.

    Returns None on any failure (tampered, expired, wrong secret, malformed).
    """
    try:
        b64, sig = cookie_value.rsplit(".", 1)
        expected = _sign(b64, secret)
        if not secrets.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(b64))
        if int(time.time()) - payload.get("ts", 0) > max_age_s:
            return None
        uid = payload.get("user_id", "")
        return uid if uid else None
    except Exception:
        return None
