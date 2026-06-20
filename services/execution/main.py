from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.db.session import async_session_factory
from shared.utils.logging import get_logger, setup_logging
from services.execution.account.context import AccountContextLoader
from services.execution.account.credential_store import CredentialStore
from services.execution.adapter.paper import PaperExecutionAdapter
from services.execution.account_stream.manager import AccountStreamManager
from services.execution.config import settings
from services.execution.consumer import ExecutionConsumer
from services.execution.events.publisher import ExecutionEventPublisher
from services.execution.health import router as health_router, set_consumer, set_recon_components
from services.execution.persistence.repository import ExecutionRepository
from services.execution.reconciliation.event_consumer import NormalizedEventConsumer
from services.execution.reconciliation.incident import IncidentLogger
from services.execution.reconciliation.loop import ReconciliationLoop
from services.execution.risk.engine import ExecutionRiskEngine
from shared.redis.client import get_redis_client

setup_logging("execution", settings.log_level)
log = get_logger("execution.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = get_redis_client()
    repository = ExecutionRepository(async_session_factory)
    risk_engine = ExecutionRiskEngine(redis)
    publisher = ExecutionEventPublisher(redis)
    incident_logger = IncidentLogger(async_session_factory)
    context_loader = AccountContextLoader(async_session_factory)
    credential_store = CredentialStore(
        async_session_factory, settings.credential_encryption_key
    )
    account_stream_manager = AccountStreamManager(
        settings=settings,
        session_factory=async_session_factory,
        redis=redis,
        credential_store=credential_store,
        incident_logger=incident_logger,
    )

    consumer = ExecutionConsumer(
        settings=settings,
        redis=redis,
        repository=repository,
        risk_engine=risk_engine,
        context_loader=context_loader,
        adapter=PaperExecutionAdapter(redis=redis),
        incident_logger=incident_logger,
    )
    recon_consumer = NormalizedEventConsumer(
        settings=settings,
        redis=redis,
        publisher=publisher,
        repository=repository,
        incident_logger=incident_logger,
    )
    recon_loop = ReconciliationLoop(
        settings=settings,
        publisher=publisher,
        repository=repository,
        incident_logger=incident_logger,
    )

    set_consumer(consumer)
    set_recon_components(recon_consumer, recon_loop)

    await account_stream_manager.start()
    consumer_task = asyncio.create_task(consumer.start())
    recon_consumer_task = asyncio.create_task(recon_consumer.start())
    recon_loop_task = asyncio.create_task(recon_loop.start())

    log.info("execution service started (3 tasks + account stream)")
    try:
        yield
    finally:
        await consumer.stop()
        await recon_consumer.stop()
        await recon_loop.stop()
        await account_stream_manager.stop()
        for task in (consumer_task, recon_consumer_task, recon_loop_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        log.info("execution service stopped")


app = FastAPI(title="execution", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
