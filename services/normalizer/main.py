"""normalizer service entry point."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from shared.utils.logging import get_logger, setup_logging
from services.normalizer.config import settings
from services.normalizer.consumer import NormalizerConsumer
from services.normalizer import health as health_module

setup_logging("normalizer", settings.log_level)
log = get_logger("normalizer.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    consumer = NormalizerConsumer(settings)
    health_module.set_stats(consumer.stats)

    task = asyncio.create_task(consumer.run(), name="normalizer-consumer")
    log.info("normalizer started")

    yield

    consumer.stop()
    try:
        await asyncio.wait_for(task, timeout=10.0)
    except asyncio.TimeoutError:
        task.cancel()
    log.info("normalizer stopped")


app = FastAPI(title="normalizer", version="0.3.0", lifespan=lifespan)

app.include_router(health_module.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "normalizer"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
