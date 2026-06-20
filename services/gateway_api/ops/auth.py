"""Admin API key auth for ops endpoints (internal only)."""
from __future__ import annotations

from fastapi import Header, HTTPException

from services.gateway_api.config import settings


async def verify_admin_api_key(x_api_key: str = Header(default="")) -> None:
    """Require X-API-Key matching ADMIN_API_KEY.

    Admin endpoints are internal-only and must not be exposed to end-users.
    Use a separate key from the regular gateway key so rotation is independent.
    """
    if x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing admin X-API-Key")
