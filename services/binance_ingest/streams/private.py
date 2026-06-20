from __future__ import annotations

import asyncio
import json

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from shared.schemas.enums import MarketType
from shared.utils.logging import get_logger

from services.binance_ingest.connection.backoff import ExponentialBackoff
from services.binance_ingest.connection.state import ConnectionInfo, ConnectionState
from services.binance_ingest.streams.publisher import RawEventPublisher

log = get_logger("binance-ingest.private")

_PING_INTERVAL = 180
_PING_TIMEOUT = 10
_CLOSE_TIMEOUT = 5


class ListenKeyManager:
    """Creates, keeps alive, and deletes a Binance user data stream listen key.

    Binance requires a PUT every 30 minutes to keep the key valid; we default
    to refreshing every 1800 s (30 min), which matches the config field
    listen_key_refresh_interval_s.
    """

    _SPOT_PATH = "/api/v3/userDataStream"
    _FUTURES_PATH = "/fapi/v1/listenKey"

    def __init__(
        self,
        rest_base: str,
        api_key: str,
        market_type: MarketType,
        refresh_interval_s: float = 1800.0,
    ) -> None:
        self._rest_base = rest_base
        self._api_key = api_key
        self._market_type = market_type
        self._refresh_interval = refresh_interval_s
        self._listen_key: str | None = None
        self._refresh_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    @property
    def listen_key(self) -> str | None:
        return self._listen_key

    @property
    def _path(self) -> str:
        return (
            self._FUTURES_PATH
            if self._market_type == MarketType.FUTURES
            else self._SPOT_PATH
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self._api_key}

    async def start(self) -> str:
        self._session = aiohttp.ClientSession(base_url=self._rest_base)
        self._listen_key = await self._create()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name=f"listen-key-refresh-{self._market_type}"
        )
        log.info("listen key created", extra={"market_type": self._market_type})
        return self._listen_key

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        if self._listen_key and self._session and not self._session.closed:
            try:
                async with self._session.delete(
                    self._path,
                    params={"listenKey": self._listen_key},
                    headers=self._headers,
                ) as resp:
                    await resp.read()  # drain
            except Exception as exc:
                log.warning("listen key delete failed", extra={"error": str(exc)})

        if self._session and not self._session.closed:
            await self._session.close()

        log.info("listen key manager stopped", extra={"market_type": self._market_type})

    async def _create(self) -> str:
        async with self._session.post(self._path, headers=self._headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["listenKey"]

    async def _keepalive(self) -> None:
        async with self._session.put(
            self._path,
            params={"listenKey": self._listen_key},
            headers=self._headers,
        ) as resp:
            resp.raise_for_status()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            try:
                await self._keepalive()
                log.info("listen key refreshed", extra={"market_type": self._market_type})
            except Exception as exc:
                log.error(
                    "listen key keepalive failed",
                    extra={"market_type": self._market_type, "error": str(exc)},
                )


class PrivateStreamHandler:
    """Connects to the Binance user data stream and publishes private events.

    Private stream messages arrive WITHOUT the combined-stream envelope.
    Event type is determined by the 'e' field in the payload.
    """

    def __init__(
        self,
        ws_base_url: str,
        market_type: MarketType,
        publisher: RawEventPublisher,
        info: ConnectionInfo,
        backoff: ExponentialBackoff,
        listen_key_manager: ListenKeyManager,
    ) -> None:
        self._ws_base = ws_base_url
        self._market_type = market_type
        self._publisher = publisher
        self._info = info
        self._backoff = backoff
        self._lkm = listen_key_manager
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        try:
            listen_key = await self._lkm.start()
        except Exception as exc:
            log.error(
                "failed to obtain listen key — private stream disabled",
                extra={"market_type": self._market_type, "error": str(exc)},
            )
            self._info.mark_stopped()
            return

        while not self._stop.is_set():
            url = f"{self._ws_base}/ws/{listen_key}"
            self._info.state = ConnectionState.CONNECTING
            try:
                async with websockets.connect(
                    url,
                    ping_interval=_PING_INTERVAL,
                    ping_timeout=_PING_TIMEOUT,
                    close_timeout=_CLOSE_TIMEOUT,
                ) as ws:
                    self._info.mark_connected()
                    self._backoff.reset()
                    log.info(
                        "private stream connected",
                        extra={"connection_id": self._info.connection_id},
                    )
                    async for raw_msg in ws:
                        if self._stop.is_set():
                            break
                        try:
                            payload = json.loads(raw_msg)
                            event_type = payload.get("e", "unknown")
                            source_stream = f"user_data.{event_type}"
                            self._info.mark_message()
                            await self._publisher.publish(
                                self._market_type, source_stream, payload
                            )
                        except Exception as exc:
                            log.warning(
                                "private message parse error",
                                extra={"error": str(exc)},
                            )
            except ConnectionClosed as exc:
                self._info.mark_reconnecting(str(exc))
                log.warning(
                    "private stream closed",
                    extra={"connection_id": self._info.connection_id, "error": str(exc)},
                )
            except OSError as exc:
                self._info.mark_reconnecting(str(exc))
                log.error(
                    "private stream OS error",
                    extra={"connection_id": self._info.connection_id, "error": str(exc)},
                )
            except Exception as exc:
                self._info.mark_reconnecting(str(exc))
                log.error(
                    "private stream unexpected error",
                    extra={"connection_id": self._info.connection_id, "error": str(exc)},
                )

            if self._stop.is_set():
                break

            delay = self._backoff.next_delay()
            log.info(
                f"private reconnecting in {delay:.1f}s",
                extra={"connection_id": self._info.connection_id},
            )
            await asyncio.sleep(delay)

        await self._lkm.stop()
        self._info.mark_stopped()
        log.info("private stream stopped", extra={"connection_id": self._info.connection_id})
