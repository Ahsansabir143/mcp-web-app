from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from shared.redis.client import RedisClient, get_redis_client, stream_read_group
from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames
from shared.schemas.events import RawEvent
from shared.utils.logging import get_logger

from services.normalizer.config import NormalizerSettings
from services.normalizer.handlers.orderbook import is_depth_delta_stream
from services.normalizer.hot_state import HotStateWriter
from services.normalizer.publisher import NormalizedEventPublisher
from services.normalizer.router import route
from services.normalizer.symbol import symbol_from_stream

log = get_logger("normalizer.consumer")


@dataclass
class ConsumerStats:
    messages_processed: int = 0
    messages_skipped: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.monotonic)
    last_message_at: float = 0.0

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self.started_at


class NormalizerConsumer:
    """Reads from stream:binance:raw, normalizes events, publishes and writes hot-state."""

    def __init__(self, settings: NormalizerSettings) -> None:
        self._settings = settings
        self._redis: RedisClient = get_redis_client()
        self._publisher = NormalizedEventPublisher(get_redis_client())
        self._hot_writer = HotStateWriter(get_redis_client())
        self._stats = ConsumerStats()
        self._stop = asyncio.Event()

    @property
    def stats(self) -> ConsumerStats:
        return self._stats

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info(
            "consumer starting",
            extra={
                "group": self._settings.consumer_group,
                "consumer": self._settings.consumer_name,
            },
        )
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._stats.errors += 1
                log.error("poll error", extra={"error": str(exc)})
                await asyncio.sleep(1)

        await self._close()

    async def _poll_once(self) -> None:
        result = await stream_read_group(
            self._redis,
            StreamNames.RAW,
            self._settings.consumer_group,
            self._settings.consumer_name,
            count=self._settings.batch_size,
            block_ms=self._settings.block_ms,
        )
        if not result:
            return

        for _stream_name, entries in result:
            for msg_id, fields in entries:
                try:
                    await self._process(msg_id, fields)
                except Exception as exc:
                    self._stats.errors += 1
                    log.error(
                        "message processing error",
                        extra={"msg_id": msg_id, "error": str(exc)},
                    )
                    # ACK even on error to avoid re-processing poison messages
                    await self._redis.xack(
                        StreamNames.RAW, self._settings.consumer_group, msg_id
                    )

    async def _process(self, msg_id: str, fields: dict) -> None:
        raw_json = fields.get("event")
        if not raw_json:
            await self._redis.xack(
                StreamNames.RAW, self._settings.consumer_group, msg_id
            )
            return

        raw_event = RawEvent.model_validate_json(raw_json)

        # Pre-fetch current book for delta handlers (read-modify-write)
        current_book: dict | None = None
        if is_depth_delta_stream(raw_event.source_stream):
            symbol = symbol_from_stream(raw_event.source_stream)
            if symbol:
                book_key = RedisKeys.market_book(raw_event.market_type.value, symbol)
                book_json = await self._redis.get(book_key)
                if book_json:
                    current_book = json.loads(book_json)

        result = route(
            raw_event,
            user_id=self._settings.default_account_id,
            current_book=current_book,
        )

        if result is None:
            self._stats.messages_skipped += 1
            log.debug(
                "unknown stream, skipping",
                extra={"source_stream": raw_event.source_stream},
            )
        else:
            await self._publisher.publish(result.event)
            await self._hot_writer.write_all(result.hot_writes)
            self._stats.messages_processed += 1
            self._stats.last_message_at = time.monotonic()

        await self._redis.xack(
            StreamNames.RAW, self._settings.consumer_group, msg_id
        )

    async def _close(self) -> None:
        await self._publisher.close()
        await self._hot_writer.close()
        await self._redis.aclose()
        log.info("consumer stopped")
