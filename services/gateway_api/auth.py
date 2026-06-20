"""API key auth + Redis sliding-window rate limiter for the gateway."""
from __future__ import annotations

import time

from fastapi import Header, HTTPException, Request

from services.gateway_api.config import settings


async def verify_gateway_api_key(
    request: Request,
    x_api_key: str = Header(default=""),
) -> str:
    """Validate X-API-Key and enforce per-minute rate limit.

    Rate-limit key: ``rate_limit:{api_key}:{minute_bucket}``
    Uses Redis INCR + EXPIRE (1-minute sliding window per bucket).
    Returns the api_key on success.
    """
    if x_api_key != settings.gateway_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    redis = request.app.state.redis
    minute_bucket = int(time.time()) // 60
    rl_key = f"rate_limit:{x_api_key}:{minute_bucket}"

    count = await redis.incr(rl_key)
    if count == 1:
        await redis.expire(rl_key, 120)  # 2 min TTL to survive clock drift

    if count > settings.rate_limit_requests_per_min:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings.rate_limit_requests_per_min} req/min",
        )

    return x_api_key
