"""Generate a short-lived HS256 JWT for local gateway testing.

Usage (from repo root):
    python scripts/gen_dev_token.py

Reads OAUTH_JWT_SECRET and OAUTH_AUDIENCE from the environment or .env file.
Prints the token to stdout so you can copy-paste it into curl / PowerShell.

Example:
    $env:OAUTH_JWT_SECRET="local-dev-secret"
    $env:OAUTH_AUDIENCE="mcp-web-gateway"
    python scripts/gen_dev_token.py
    # or with specific scopes:
    python scripts/gen_dev_token.py --scope "mcp:account:read mcp:tools:read"
    python scripts/gen_dev_token.py --ttl 7200
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a dev HS256 JWT for the MCP web gateway")
    parser.add_argument("--secret", default=None, help="HS256 secret (default: $OAUTH_JWT_SECRET)")
    parser.add_argument("--audience", default=None, help="JWT audience (default: $OAUTH_AUDIENCE)")
    parser.add_argument("--sub", default="dev-user", help="JWT subject (default: dev-user)")
    parser.add_argument("--client-id", default="dev-client", help="azp claim (default: dev-client)")
    parser.add_argument(
        "--scope",
        default="mcp:tools:read mcp:account:read mcp:strategy:read",
        help="Space-separated scopes (default: all three read scopes)",
    )
    parser.add_argument("--ttl", type=int, default=3600, help="Token lifetime in seconds (default: 3600)")
    args = parser.parse_args()

    # Load .env if present
    _load_dotenv(Path(".env"))

    try:
        from jose import jwt as jose_jwt
    except ImportError:
        print("ERROR: python-jose not installed.  Run: pip install python-jose[cryptography]", file=sys.stderr)
        sys.exit(1)

    secret = args.secret or os.environ.get("OAUTH_JWT_SECRET", "")
    audience = args.audience or os.environ.get("OAUTH_AUDIENCE", "mcp-web-gateway")

    if not secret:
        print(
            "ERROR: No OAUTH_JWT_SECRET found.\n"
            "Set it in .env or pass --secret <value>",
            file=sys.stderr,
        )
        sys.exit(1)

    now = int(time.time())
    payload = {
        "sub": args.sub,
        "azp": args.client_id,
        "scope": args.scope,
        "aud": audience,
        "iat": now,
        "exp": now + args.ttl,
    }
    token = jose_jwt.encode(payload, secret, algorithm="HS256")

    print("\n── Dev token ─────────────────────────────────────────────────────────")
    print(token)
    print("──────────────────────────────────────────────────────────────────────")
    print(f"\n  sub:      {args.sub}")
    print(f"  client:   {args.client_id}")
    print(f"  scope:    {args.scope}")
    print(f"  audience: {audience}")
    print(f"  expires:  {args.ttl}s from now")
    print("\nUsage:")
    print(f'  $TOKEN = "{token}"')
    print("  curl -s http://localhost:8007/health")
    print("  curl -s -N -H \"Authorization: Bearer $TOKEN\" http://localhost:8007/sse")
    print()


if __name__ == "__main__":
    main()
