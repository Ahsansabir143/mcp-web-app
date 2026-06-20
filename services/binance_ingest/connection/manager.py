from __future__ import annotations

import asyncio

from shared.schemas.enums import MarketType
from shared.utils.logging import get_logger

from services.binance_ingest.config import BinanceIngestSettings
from services.binance_ingest.connection.backoff import ExponentialBackoff
from services.binance_ingest.connection.state import ConnectionInfo
from services.binance_ingest.streams.private import ListenKeyManager, PrivateStreamHandler
from services.binance_ingest.streams.public import PublicStreamSubscriber
from services.binance_ingest.streams.publisher import RawEventPublisher

log = get_logger("binance-ingest.manager")


class ConnectionManager:
    """Owns and supervises all WebSocket connections as asyncio Tasks.

    Each PublicStreamSubscriber / PrivateStreamHandler already implements its own
    reconnect loop, so the manager only needs to track tasks for clean shutdown.
    """

    def __init__(self, settings: BinanceIngestSettings) -> None:
        self._settings = settings
        self._publisher = RawEventPublisher()
        self._connections: dict[str, ConnectionInfo] = {}
        self._tasks: list[asyncio.Task] = []

    @property
    def connections(self) -> dict[str, ConnectionInfo]:
        return self._connections

    def _ws_base(self, market_type: MarketType) -> str:
        s = self._settings
        if market_type == MarketType.FUTURES:
            return s.binance_ws_testnet_futures if s.binance_use_testnet else s.binance_ws_futures_base
        return s.binance_ws_testnet_spot if s.binance_use_testnet else s.binance_ws_spot_base

    def _rest_base(self, market_type: MarketType) -> str:
        s = self._settings
        if market_type == MarketType.FUTURES:
            return s.binance_rest_testnet_futures if s.binance_use_testnet else s.binance_rest_futures
        return s.binance_rest_testnet_spot if s.binance_use_testnet else s.binance_rest_spot

    def _make_backoff(self) -> ExponentialBackoff:
        s = self._settings
        return ExponentialBackoff(
            base_s=s.reconnect_delay_s,
            max_s=s.reconnect_max_delay_s,
            factor=s.reconnect_factor,
        )

    def _add_public(
        self, conn_id: str, market_type: MarketType, streams: list[str]
    ) -> None:
        info = ConnectionInfo(
            connection_id=conn_id,
            market_type=market_type.value,
            stream_type="public",
        )
        self._connections[conn_id] = info
        subscriber = PublicStreamSubscriber(
            ws_base_url=self._ws_base(market_type),
            streams=streams,
            market_type=market_type,
            publisher=self._publisher,
            info=info,
            backoff=self._make_backoff(),
        )
        task = asyncio.create_task(subscriber.run(), name=conn_id)
        self._tasks.append(task)

    def _add_private(
        self, conn_id: str, market_type: MarketType, api_key: str
    ) -> None:
        info = ConnectionInfo(
            connection_id=conn_id,
            market_type=market_type.value,
            stream_type="private",
        )
        self._connections[conn_id] = info
        lkm = ListenKeyManager(
            rest_base=self._rest_base(market_type),
            api_key=api_key,
            market_type=market_type,
            refresh_interval_s=self._settings.listen_key_refresh_interval_s,
        )
        handler = PrivateStreamHandler(
            ws_base_url=self._ws_base(market_type),
            market_type=market_type,
            publisher=self._publisher,
            info=info,
            backoff=self._make_backoff(),
            listen_key_manager=lkm,
        )
        task = asyncio.create_task(handler.run(), name=conn_id)
        self._tasks.append(task)

    async def start(
        self, spot_streams: list[str], futures_streams: list[str]
    ) -> None:
        if spot_streams:
            self._add_public("spot-public", MarketType.SPOT, spot_streams)
            log.info(
                "spot public subscriber started",
                extra={"n_streams": len(spot_streams)},
            )

        if futures_streams:
            self._add_public("futures-public", MarketType.FUTURES, futures_streams)
            log.info(
                "futures public subscriber started",
                extra={"n_streams": len(futures_streams)},
            )

        s = self._settings
        if s.binance_api_key:
            self._add_private("spot-private", MarketType.SPOT, s.binance_api_key)
            log.info("spot private stream started")

        if s.binance_futures_api_key:
            self._add_private(
                "futures-private", MarketType.FUTURES, s.binance_futures_api_key
            )
            log.info("futures private stream started")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._publisher.close()
        log.info("connection manager stopped")
