"""Redis-backed storage for OAuth auth codes and bearer tokens.

Key schema:
  mcp:oauth:code:<code>   → JSON {code_challenge, redirect_uri, client_id, user_id, state}  TTL: auth_code_ttl_s
  mcp:oauth:token:<token> → JSON {user_id, client_id, scope, created_at, expires_at}          TTL: access_token_ttl_s
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time


def _code_key(code: str) -> str:
    return f"mcp:oauth:code:{code}"


def _token_key(token: str) -> str:
    return f"mcp:oauth:token:{token}"


def generate_auth_code() -> str:
    return secrets.token_urlsafe(32)


def generate_bearer_token() -> str:
    return secrets.token_urlsafe(48)


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE S256: BASE64URL(SHA256(ASCII(code_verifier))) == code_challenge."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


async def store_auth_code(
    redis,
    code: str,
    *,
    code_challenge: str,
    redirect_uri: str,
    client_id: str,
    user_id: str,
    state: str,
    ttl_s: int,
) -> None:
    payload = json.dumps({
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "user_id": user_id,
        "state": state,
        "created_at": int(time.time()),
    })
    await redis.setex(_code_key(code), ttl_s, payload)


async def consume_auth_code(redis, code: str) -> dict | None:
    """Atomically read and delete an auth code. Returns None if not found or expired."""
    key = _code_key(code)
    pipe = redis.pipeline(transaction=True)
    pipe.get(key)
    pipe.delete(key)
    results = await pipe.execute()
    raw = results[0]
    if not raw:
        return None
    return json.loads(raw)


async def store_bearer_token(
    redis,
    token: str,
    *,
    user_id: str,
    client_id: str,
    scope: str,
    ttl_s: int,
) -> None:
    now = int(time.time())
    payload = json.dumps({
        "user_id": user_id,
        "client_id": client_id,
        "scope": scope,
        "created_at": now,
        "expires_at": now + ttl_s,
    })
    await redis.setex(_token_key(token), ttl_s, payload)


async def lookup_bearer_token(redis, token: str) -> dict | None:
    """Return token payload dict, or None if expired or missing."""
    raw = await redis.get(_token_key(token))
    if not raw:
        return None
    return json.loads(raw)
