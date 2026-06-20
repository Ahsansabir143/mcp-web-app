from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.utils.logging import get_logger, setup_logging
from services.analytics.config import settings
from services.analytics.consumer import AnalyticsConsumer
from services.analytics.health import router as health_router, set_consumer

setup_logging("analytics", settings.log_level)
log = get_logger("analytics.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = AnalyticsConsumer(settings)
    set_consumer(consumer)
    task = asyncio.create_task(consumer.start())
    log.info("analytics service started")
    try:
        yield
    finally:
        await consumer.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("analytics service stopped")


app = FastAPI(title="analytics", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
