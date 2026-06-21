from fastapi import APIRouter

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    from .config import settings
    from .session import session_count

    if settings.oauth_jwks_url:
        oauth_mode = "jwks"
    elif settings.oauth_issuer_url:
        oauth_mode = "jwks-derived"
    elif settings.oauth_jwt_secret:
        oauth_mode = "hs256-secret"
    else:
        oauth_mode = "unconfigured"

    return {
        "status": "ok",
        "service": "mcp-web-gateway",
        "version": "1.0.0",
        "oauth_mode": oauth_mode,
        "active_sessions": session_count(),
        "max_sessions": settings.max_sessions,
    }
