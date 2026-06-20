"""Gateway health endpoints — aggregates Redis + DB connectivity."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health")
async def health():
    return {"status": "ok", "service": "gateway-api"}


@router.get("/api/health/detail")
async def health_detail(request: Request):
    redis = request.app.state.redis
    session_factory = request.app.state.session_factory

    redis_ok = False
    db_ok = False

    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        pass

    try:
        async with session_factory() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    overall = "ok" if (redis_ok and db_ok) else "degraded"
    return {
        "status": overall,
        "service": "gateway-api",
        "dependencies": {
            "redis": "ok" if redis_ok else "error",
            "db": "ok" if db_ok else "error",
        },
    }
