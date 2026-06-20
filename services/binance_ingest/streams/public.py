from __future__ import annotations

import asyncio
import json

import websockets
from websockets.exceptions import ConnectionClosed

from shared.schemas.enums import MarketType
from shared.utils.logging import get_logger

from services.binance_ingest.connection.backoff import ExponentialBackoff
from services.binance_ingest.connection.state import ConnectionInfo, ConnectionState
from services.binance_ingest.streams.publisher import RawEventPublisher

log = get_logger("binance-ingest.public")

# Binance closes idle connections after 24 h; 3-min pings keep them alive.
_PING_INTERVAL = 180
_PING_TIMEOUT = 10
_CLOSE_TIMEOUT = 5


class PublicStreamSubscriber:
    """Connects to Binance combined-stream endpoint and publishes raw events.

    All public Binance messages use the combined-stream envelope:
        {"stream": "<name>", "data": {...}}
    This subscriber unwraps the envelope and forwards the payload.
    """

    def __init__(
        self,
        ws_base_url: str,
        streams: list[str],
        market_type: MarketType,
        publisher: RawEventPublisher,
        info: ConnectionInfo,
        backoff: ExponentialBackoff,
    ) -> None:
        self._ws_base = ws_base_url
        self._streams = streams
        self._market_type = market_type
        self._publisher = publisher
        self._info = info
        self._backoff = backoff
        self._stop = asyncio.Event()

    @property
    def url(self) -> str:
        streams_param = "/".join(s.lower() for s in self._streams)
        return f"{self._ws_base}/stream?streams={streams_param}"

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            self._info.state = ConnectionState.CONNECTING
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=_PING_INTERVAL,
                    ping_timeout=_PING_TIMEOUT,
                    close_timeout=_CLOSE_TIMEOUT,
                ) as ws:
                    self._info.mark_connected()
                    self._backoff.reset()
                    log.info(
                        "public stream connected",
                        extra={
                            "connection_id": self._info.connection_id,
                            "market_type": self._market_type,
                            "n_streams": len(self._streams),
                        },
                    )
                    async for raw_msg in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw_msg)
                            # Combined stream envelope
                            source_stream = msg.get("stream", "unknown")
                            payload = msg.get("data", msg)
                            self._info.mark_message()
                            await self._publisher.publish(
                                self._market_type, source_stream, payload
                            )
                        except Exception as exc:
                            log.warning(
                                "message parse error",
                                extra={"error": str(exc)},
                            )
            except ConnectionClosed as exc:
                self._info.mark_reconnecting(str(exc))
                log.warning(
                    "public stream closed",
                    extra={"connection_id": self._info.connection_id, "error": str(exc)},
                )
            except OSError as exc:
                self._info.mark_reconnecting(str(exc))
                log.error(
                    "public stream OS error",
                    extra={"connection_id": self._info.connection_id, "error": str(exc)},
                )
            except Exception as exc:
                self._info.mark_reconnecting(str(exc))
                log.error(
                    "public stream unexpected error",
                    extra={"connection_id": self._info.connection_id, "error": str(exc)},
                )

            if self._stop.is_set():
                break

            delay = self._backoff.next_delay()
            log.info(
                f"reconnecting in {delay:.1f}s",
                extra={"connection_id": self._info.connection_id},
            )
            await asyncio.sleep(delay)

        self._info.mark_stopped()
        log.info("public stream stopped", extra={"connection_id": self._info.connection_id})
