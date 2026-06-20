from __future__ import annotations

import uuid

from shared.redis.client import RedisClient, stream_publish
from shared.redis.streams import StreamNames
from shared.schemas.strategy import TradeIntent


class IntentPublisher:
    """Publishes TradeIntent objects to stream:strategy:intents."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def publish(
        self,
        intent: TradeIntent,
        evaluation_id: uuid.UUID | None = None,
    ) -> None:
        await stream_publish(
            self._redis,
            StreamNames.STRATEGY_INTENTS,
            {
                "intent": intent.model_dump_json(),
                "evaluation_id": str(evaluation_id) if evaluation_id else "",
                "strategy_id": str(intent.strategy_id) if intent.strategy_id else "",
            },
        )
