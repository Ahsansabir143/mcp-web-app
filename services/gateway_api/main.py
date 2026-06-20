"""Gateway API — thin REST surface over the MCP server facades.

All routes require X-API-Key authentication and are subject to per-minute
rate limiting enforced in services.gateway_api.auth.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.db.session import async_session_factory
from shared.redis.client import get_redis_client
from shared.utils.logging import get_logger, setup_logging
from services.gateway_api.config import settings
from services.gateway_api.health import router as health_router
from services.gateway_api.ops.jobs import router as ops_jobs_router
from services.gateway_api.ops.kill_switch import router as ops_kill_switch_router
from services.gateway_api.ops.metrics import router as ops_metrics_router
from services.gateway_api.ops.streams import router as ops_streams_router
from services.gateway_api.ops.strategy_status import router as ops_strategy_router
from services.gateway_api.ops.trading_mode import router as ops_trading_mode_router
from services.gateway_api.routes.executions import router as executions_router
from services.gateway_api.routes.market import router as market_router
from services.gateway_api.routes.paper_trade import router as paper_trade_router
from services.gateway_api.routes.strategies import router as strategies_router

setup_logging("gateway-api", settings.log_level)
log = get_logger("gateway-api.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = get_redis_client()
    app.state.session_factory = async_session_factory
    log.info("gateway-api started")
    yield
    log.info("gateway-api stopped")


app = FastAPI(
    title="Trading Platform Gateway API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(market_router)
app.include_router(strategies_router)
app.include_router(executions_router)
app.include_router(paper_trade_router)
# Internal ops endpoints — require admin API key, not exposed to end-users
app.include_router(ops_streams_router)
app.include_router(ops_strategy_router)
app.include_router(ops_jobs_router)
app.include_router(ops_trading_mode_router)
app.include_router(ops_kill_switch_router)
app.include_router(ops_metrics_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
