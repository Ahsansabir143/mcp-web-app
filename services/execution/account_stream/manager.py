"""AccountStreamManager — starts/stops user-data stream listeners per account.

At startup, queries all active exchange accounts that have credentials, then
launches one AccountStreamListener per account as an asyncio Task.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.account import ExchangeAccount
from shared.utils.logging import get_logger
from services.execution.account.credential_store import CredentialStore
from services.execution.account_stream.listener import AccountStreamListener
from services.execution.config import ExecutionSettings

log = get_logger("execution.account_stream.manager")


class AccountStreamManager:
    """Manages user-data WebSocket streams for all accounts with stored credentials."""

    def __init__(
        self,
        settings: ExecutionSettings,
        session_factory: async_sessionmaker[AsyncSession],
        redis,
        credential_store: CredentialStore,
        incident_logger=None,
    ) -> None:
        self._settings = settings
        self._factory = session_factory
        self._redis = redis
        self._cred_store = credential_store
        self._incident_logger = incident_logger
        self._listeners: dict[str, AccountStreamListener] = {}
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if not self._settings.account_stream_enabled:
            log.info("account stream disabled (ACCOUNT_STREAM_ENABLED=false)")
            return
        if not self._cred_store.is_configured():
            log.warning("account stream enabled but CREDENTIAL_ENCRYPTION_KEY not set — skipping")
            return

        async with self._factory() as session:
            stmt = select(ExchangeAccount).where(ExchangeAccount.is_active == True)
            accounts = (await session.execute(stmt)).scalars().all()

        for acct in accounts:
            creds = await self._cred_store.get(acct.id)
            if creds is None:
                log.debug("no credentials for account — skipping stream", account_id=str(acct.id))
                continue

            api_key, _ = creds  # api_key only needed here; listener gets full creds
            creds_full = await self._cred_store.get(acct.id)
            if creds_full is None:
                continue

            market_type = acct.venue == "binance_futures" and "futures" or "spot"
            ws_base = (
                self._settings.binance_ws_futures_base
                if market_type == "futures"
                else self._settings.binance_ws_spot_base
            )
            rest_base = (
                self._settings.binance_rest_futures
                if market_type == "futures"
                else self._settings.binance_rest_spot
            )

            listener = AccountStreamListener(
                account_id=acct.id,
                api_key=creds_full[0],
                api_secret=creds_full[1],
                market_type=market_type,
                ws_base=ws_base,
                rest_base=rest_base,
                session_factory=self._factory,
                redis=self._redis,
                incident_logger=self._incident_logger,
                listen_key_refresh_interval_s=self._settings.listen_key_refresh_interval_s,
            )
            self._listeners[str(acct.id)] = listener
            task = asyncio.create_task(
                listener.run(), name=f"account-stream-{acct.id}"
            )
            self._tasks.append(task)
            log.info("account stream started", account_id=str(acct.id), market_type=market_type)

    async def stop(self) -> None:
        for listener in self._listeners.values():
            listener.stop()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        log.info("account stream manager stopped")
