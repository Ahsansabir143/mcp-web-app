from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.db.session import async_session_factory
from shared.utils.logging import get_logger, setup_logging
from services.strategy_service.config import settings
from services.strategy_service.consumer import StrategyConsumer
from services.strategy_service.health import router as health_router, set_consumer
from services.strategy_service.persistence.repository import StrategyRepository

setup_logging("strategy-service", settings.log_level)
log = get_logger("strategy-service.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = StrategyRepository(async_session_factory)
    consumer = StrategyConsumer(settings, repository=repository)
    set_consumer(consumer)
    task = asyncio.create_task(consumer.start())
    log.info("strategy service started")
    try:
        yield
    finally:
        await consumer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("strategy service stopped")


app = FastAPI(title="strategy-service", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
