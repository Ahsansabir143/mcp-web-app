"""Redis hot-state reader for real-time account position data."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys
from shared.utils.logging import get_logger

log = get_logger("execution.account.state_reader")


class AccountStateReader:
    """Reads live account state from Redis hot-state written by the normalizer.

    Results are used as inputs to the risk engine's concurrent-exposure and
    daily-PnL checks. All methods return ``None`` when no data is available so
    callers can fall back to placeholder behaviour gracefully.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def get_open_position_count(self, user_id: str) -> int | None:
        """Count non-zero positions from the account positions hot-state key."""
        raw = await self._redis.get(RedisKeys.account_positions(user_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            positions = data.get("positions", [])
            return sum(
                1
                for p in positions
                if float(p.get("position_amt", "0")) != 0.0
            )
        except Exception:
            return None

    async def get_accumulated_realized_pnl(self, user_id: str) -> Decimal | None:
        """Sum ``accumulated_realized`` across all position records.

        Note: this is the lifetime accumulated realized PnL stored in each open
        position snapshot by the normalizer — *not* strictly today's daily loss.
        Full intraday daily-loss tracking requires fill-history aggregation which
        is deferred to a future phase.
        """
        raw = await self._redis.get(RedisKeys.account_positions(user_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            positions = data.get("positions", [])
            total = Decimal("0")
            for p in positions:
                val = p.get("accumulated_realized", "0")
                total += Decimal(str(val))
            return total
        except (InvalidOperation, Exception):
            return None
