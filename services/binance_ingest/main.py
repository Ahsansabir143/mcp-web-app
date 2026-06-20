"""binance-ingest service entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from shared.utils.logging import get_logger, setup_logging
from services.binance_ingest.config import settings
from services.binance_ingest.connection.manager import ConnectionManager
from services.binance_ingest import health as health_module

setup_logging("binance-ingest", settings.log_level)
log = get_logger("binance-ingest.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    manager = ConnectionManager(settings)
    health_module.set_manager(manager)

    spot_streams = [s.strip() for s in settings.spot_streams.split(",") if s.strip()]
    futures_streams = [s.strip() for s in settings.futures_streams.split(",") if s.strip()]

    await manager.start(spot_streams, futures_streams)
    log.info(
        "binance-ingest started",
        extra={
            "spot_streams": len(spot_streams),
            "futures_streams": len(futures_streams),
        },
    )

    yield

    await manager.stop()
    log.info("binance-ingest stopped")


app = FastAPI(title="binance-ingest", version="0.2.0", lifespan=lifespan)

app.include_router(health_module.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "binance-ingest"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
