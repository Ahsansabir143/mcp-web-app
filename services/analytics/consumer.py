from __future__ import annotations

import asyncio
import json
import time

from shared.redis.client import RedisClient, get_redis_client, stream_read_group
from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames
from shared.schemas.enums import EventType
from shared.schemas.events import NormalizedEvent
from shared.utils.logging import get_logger
from services.analytics.config import AnalyticsSettings
from services.analytics.dispatcher import AnalyticsDispatcher
from services.analytics.hot_state import AnalyticsHotStateWriter
from services.analytics.publisher import DerivedEventPublisher
from services.analytics.snapshot.builder import SnapshotBuilder
from services.analytics.state import StateStore

_BOOK_EVENTS = {EventType.ORDERBOOK_SNAPSHOT, EventType.ORDERBOOK_DELTA}

log = get_logger("analytics.consumer")


class AnalyticsConsumer:
    """Reads normalized events, updates in-memory state, publishes snapshots.

    Loop:
      1. XREADGROUP from stream:binance:normalized
      2. For book events, pre-fetch merged book from Redis
      3. Dispatch to AnalyticsDispatcher (sync)
      4. XACK
      5. Every snapshot_publish_interval_s: build + publish snapshots for all active symbols
    """

    def __init__(
        self,
        settings: AnalyticsSettings | None = None,
        redis: RedisClient | None = None,
    ) -> None:
        self._settings = settings or AnalyticsSettings()
        self._redis = redis or get_redis_client()
        self._store = StateStore(
            flow_cvd_window=self._settings.cvd_window_trades,
            wall_min_notional=self._settings.wall_min_notional_usd,
            wall_depth_levels=self._settings.wall_depth_levels,
            rvol_lookback=self._settings.rvol_lookback_candles,
        )
        self._dispatcher = AnalyticsDispatcher(self._store)
        self._builder = SnapshotBuilder()
        self._publisher = DerivedEventPublisher(self._redis)
        self._hot_state = AnalyticsHotStateWriter(self._redis)
        self._last_publish_at: float = 0.0
        self._running = False

    async def start(self) -> None:
        self._running = True
        log.info("analytics consumer starting")
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("consumer tick error", exc_info=exc)
                await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        messages = await stream_read_group(
            self._redis,
            StreamNames.NORMALIZED,
            self._settings.consumer_group,
            self._settings.consumer_name,
            count=self._settings.batch_size,
            block_ms=self._settings.block_ms,
        )

        for _stream, entries in (messages or []):
            for msg_id, fields in entries:
                await self._process(msg_id, fields)

        now = time.monotonic()
        if now - self._last_publish_at >= self._settings.snapshot_publish_interval_s:
            self._last_publish_at = now
            await self._publish_all(int(time.time() * 1000))

    async def _process(self, msg_id: str, fields: dict) -> None:
        raw_event = fields.get("event", "")
        if not raw_event:
            await self._ack(msg_id)
            return
        try:
            event = NormalizedEvent.model_validate_json(raw_event)
        except Exception as exc:
            log.warning("failed to parse normalized event", exc_info=exc)
            await self._ack(msg_id)
            return

        current_book: dict | None = None
        if event.event_type in _BOOK_EVENTS:
            current_book = await self._fetch_book(event)

        self._dispatcher.update(event, current_book)
        await self._ack(msg_id)

    async def _fetch_book(self, event: NormalizedEvent) -> dict | None:
        key = RedisKeys.market_book(event.market_type.value, event.symbol)
        try:
            raw = await self._redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    async def _ack(self, msg_id: str) -> None:
        try:
            await self._redis.xack(
                StreamNames.NORMALIZED,
                self._settings.consumer_group,
                msg_id,
            )
        except Exception as exc:
            log.warning("xack failed", exc_info=exc)

    async def _publish_all(self, now_ms: int) -> None:
        account_id = self._settings.default_account_id
        for state in self._store.all_states():
            try:
                snapshot = await self._builder.build(state, self._redis, account_id, now_ms)
                snapshot_dict = snapshot.model_dump(mode="json")
                await self._hot_state.write(state, snapshot_dict, now_ms)
                await self._publisher.publish(snapshot_dict)
            except Exception as exc:
                log.error(
                    "snapshot publish error",
                    symbol=state.symbol,
                    market_type=state.market_type,
                    exc_info=exc,
                )
