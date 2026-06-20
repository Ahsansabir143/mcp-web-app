from __future__ import annotations

import json

from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys
from services.analytics.state import SymbolState

_TTL_ANALYTICS = 60
_TTL_INDICATORS = 3600


class AnalyticsHotStateWriter:
    """Writes analytics hot-state keys to Redis from current SymbolState.

    Writes 8 key types per symbol: cvd, delta, rvol, liquidation_clusters,
    walls, funding_pressure, snapshot, and per-interval indicators.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def write(self, state: SymbolState, snapshot_dict: dict, now_ms: int) -> None:
        mtype = state.market_type
        sym = state.symbol

        async with self._redis.pipeline(transaction=False) as pipe:
            # CVD
            pipe.set(
                RedisKeys.analytics_cvd(mtype, sym),
                json.dumps({"cvd": state.flow.cvd, "timestamp_ms": now_ms}),
                ex=_TTL_ANALYTICS,
            )

            # Delta
            pipe.set(
                RedisKeys.analytics_delta(mtype, sym),
                json.dumps({"delta": state.flow.compute_delta(), "timestamp_ms": now_ms}),
                ex=_TTL_ANALYTICS,
            )

            # RVOL
            rvol = state.rvol.compute()
            if rvol is not None:
                pipe.set(
                    RedisKeys.analytics_rvol(mtype, sym),
                    json.dumps({"rvol": rvol, "timestamp_ms": now_ms}),
                    ex=_TTL_ANALYTICS,
                )

            # Liquidation clusters
            if state.last_price and state.last_price > 0:
                long_usd, short_usd = state.liquidations.compute_cluster_totals(
                    state.last_price, now_ms
                )
                pipe.set(
                    RedisKeys.analytics_liquidation_clusters(mtype, sym),
                    json.dumps({
                        "long_usd": long_usd,
                        "short_usd": short_usd,
                        "timestamp_ms": now_ms,
                    }),
                    ex=_TTL_ANALYTICS,
                )

            # Walls
            walls_bid: list = []
            walls_ask: list = []
            spoofing = False
            book_state = snapshot_dict.get("book_state", {})
            if isinstance(book_state, dict):
                walls_bid = book_state.get("top_bid_walls", [])
                walls_ask = book_state.get("top_ask_walls", [])
                spoofing = book_state.get("spoofing_alert", False)
            pipe.set(
                RedisKeys.analytics_walls(mtype, sym),
                json.dumps({
                    "bid_walls": walls_bid,
                    "ask_walls": walls_ask,
                    "spoofing_alert": spoofing,
                    "timestamp_ms": now_ms,
                }, default=str),
                ex=_TTL_ANALYTICS,
            )

            # Funding pressure
            futures_state = snapshot_dict.get("futures_state", {})
            if isinstance(futures_state, dict):
                fp = futures_state.get("funding_pressure_score")
                if fp is not None:
                    pipe.set(
                        RedisKeys.analytics_funding_pressure(mtype, sym),
                        json.dumps({"funding_pressure_score": str(fp), "timestamp_ms": now_ms}),
                        ex=_TTL_ANALYTICS,
                    )

            # Full snapshot
            pipe.set(
                RedisKeys.analytics_snapshot(mtype, sym),
                json.dumps(snapshot_dict, default=str),
                ex=_TTL_ANALYTICS,
            )

            await pipe.execute()

        # Indicator keys per interval (separate pipeline)
        if state.indicators:
            async with self._redis.pipeline(transaction=False) as pipe:
                for interval, engine in state.indicators.items():
                    vals = engine.compute()
                    if any(v is not None for v in (
                        vals.ema_9, vals.rsi_14, vals.vwap, vals.atr_14
                    )):
                        pipe.set(
                            RedisKeys.analytics_indicators(mtype, sym, interval),
                            json.dumps({
                                "ema_9": vals.ema_9,
                                "ema_21": vals.ema_21,
                                "ema_50": vals.ema_50,
                                "ema_200": vals.ema_200,
                                "rsi_14": vals.rsi_14,
                                "vwap": vals.vwap,
                                "bb_upper": vals.bb_upper,
                                "bb_middle": vals.bb_middle,
                                "bb_lower": vals.bb_lower,
                                "bb_width": vals.bb_width,
                                "atr_14": vals.atr_14,
                                "timestamp_ms": now_ms,
                            }),
                            ex=_TTL_INDICATORS,
                        )
                await pipe.execute()
